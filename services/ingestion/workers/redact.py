"""Two-layer PII redactor.

Layer 1 (regex): deterministic patterns — email, phone, SSN, credit-card with
Luhn check, IPv4/IPv6, common API-key shapes. ~0ms latency.

Layer 2 (Presidio): NER for PERSON / LOCATION / ORGANIZATION / DATE_OF_BIRTH.
Loaded lazily — if `presidio-analyzer` isn't installed (test/dev env), this
layer is skipped and the redactor logs a one-time warning.

Truncation policy: previews are truncated to 500 chars BEFORE redaction.
Presidio's cost is super-linear in input length and the schema caps at 500
anyway.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger("worker.redact")

PREVIEW_MAX = 500

# Tokens that look like PII to a naive scanner but are not — provider/model
# names, common debug strings. The allowlist is checked AFTER tokenization so
# that "openai" inside an email address still gets redacted.
_ALLOWLIST = frozenset(
    {
        "openai",
        "anthropic",
        "google",
        "deepseek",
        "xai",
        "claude",
        "gpt",
        "gemini",
        "grok",
        "sonnet",
        "haiku",
        "opus",
    }
)


def _luhn_check(number: str) -> bool:
    """Validate a digit string with the Luhn algorithm. Used to keep
    16-digit non-CC numbers (e.g. tracking IDs) from being falsely flagged."""
    digits = [int(d) for d in number if d.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


@dataclass(frozen=True)
class _Pattern:
    name: str
    regex: re.Pattern[str]
    placeholder: str
    requires_luhn: bool = False


# Order matters: longer/more-specific patterns must match before shorter ones.
_REGEX_PATTERNS: tuple[_Pattern, ...] = (
    _Pattern(
        "EMAIL",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "[EMAIL]",
    ),
    _Pattern(
        "API_KEY",
        re.compile(
            r"\b("
            r"sk-[A-Za-z0-9_-]{20,}"
            r"|sk-ant-[A-Za-z0-9_-]{20,}"
            r"|AKIA[0-9A-Z]{16}"
            r"|gsk_[A-Za-z0-9]{20,}"
            r"|xoxb-[A-Za-z0-9-]{20,}"
            r")\b"
        ),
        "[API_KEY]",
    ),
    _Pattern(
        "CREDIT_CARD",
        re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
        "[CREDIT_CARD]",
        requires_luhn=True,
    ),
    _Pattern(
        "SSN",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "[SSN]",
    ),
    _Pattern(
        "PHONE",
        re.compile(
            r"(?:\+?\d{1,3}[-.\s]?)?"
            r"(?:\(\d{2,4}\)|\d{2,4})[-.\s]?\d{3,4}[-.\s]?\d{3,4}"
        ),
        "[PHONE]",
    ),
    _Pattern(
        "IPV4",
        re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
        "[IP]",
    ),
    _Pattern(
        "IPV6",
        re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b"),
        "[IP]",
    ),
)


def _apply_regex_layer(text: str) -> str:
    for pattern in _REGEX_PATTERNS:
        def _sub(match: re.Match[str], p: _Pattern = pattern) -> str:
            captured = match.group(0)
            if p.requires_luhn and not _luhn_check(captured):
                return captured
            return p.placeholder
        text = pattern.regex.sub(_sub, text)
    return text


class _PresidioBackend:
    """Lazy wrapper around presidio-analyzer. Caches the analyzer instance."""

    def __init__(self) -> None:
        self._analyzer = None
        self._unavailable = False

    def _load(self) -> object | None:
        if self._unavailable:
            return None
        if self._analyzer is not None:
            return self._analyzer
        try:
            from presidio_analyzer import AnalyzerEngine

            self._analyzer = AnalyzerEngine()
            return self._analyzer
        except Exception:
            logger.warning(
                "Presidio unavailable; falling back to regex-only redaction. "
                "Install the 'nlp' extra and the en_core_web_lg spaCy model "
                "to enable NER redaction."
            )
            self._unavailable = True
            return None

    def redact(self, text: str) -> str:
        analyzer = self._load()
        if analyzer is None:
            return text
        results = analyzer.analyze(  # type: ignore[attr-defined]
            text=text,
            entities=["PERSON", "LOCATION", "ORGANIZATION", "DATE_OF_BIRTH"],
            language="en",
        )
        if not results:
            return text
        # Apply replacements right-to-left so spans stay valid.
        results = sorted(results, key=lambda r: r.start, reverse=True)
        chars = list(text)
        for r in results:
            placeholder = f"[{r.entity_type}]"
            chars[r.start : r.end] = list(placeholder)
        return "".join(chars)


_presidio = _PresidioBackend()


def redact(text: str | None) -> str | None:
    """Redact PII from text. Returns None unchanged.

    1. Truncate to PREVIEW_MAX.
    2. Regex layer (always on).
    3. Presidio NER layer (skipped if not installed).
    4. Allowlist sanity check (left for caller; the redactor itself doesn't
       try to un-redact since false-positives are safer than false-negatives).
    """
    if text is None:
        return None
    truncated = text[:PREVIEW_MAX]
    stage1 = _apply_regex_layer(truncated)
    return _presidio.redact(stage1)


def is_allowlisted(token: str) -> bool:
    return token.lower() in _ALLOWLIST
