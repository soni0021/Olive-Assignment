"""PII redactor tests.

The regex layer is exercised directly. Presidio is mocked because installing
spaCy + en_core_web_lg in CI would balloon test time and image size.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from workers import redact as redact_module
from workers.redact import PREVIEW_MAX, is_allowlisted, redact


@pytest.fixture(autouse=True)
def disable_presidio(monkeypatch):
    """Force Presidio to report unavailable so tests are regex-only."""
    backend = redact_module._presidio
    monkeypatch.setattr(backend, "_unavailable", True)
    monkeypatch.setattr(backend, "_analyzer", None)


def test_none_passes_through():
    assert redact(None) is None


def test_email_redacted():
    assert "[EMAIL]" in redact("Contact me at john.doe@example.test for details.")
    assert "john.doe" not in redact("Contact me at john.doe@example.test for details.")


def test_us_phone_redacted():
    out = redact("Call 555-555-0100 anytime.")
    assert "[PHONE]" in out
    assert "555-555-0100" not in out


def test_international_phone_redacted():
    out = redact("Call +44 20 7946 0958 or +1 (415) 555-0100.")
    assert "[PHONE]" in out
    assert "555-0100" not in out


def test_ssn_redacted():
    out = redact("SSN: 123-45-6789")
    assert "[SSN]" in out
    assert "123-45-6789" not in out


def test_credit_card_with_luhn_redacted():
    # 4111 1111 1111 1111 is the canonical test Visa, passes Luhn.
    out = redact("Card on file: 4111 1111 1111 1111")
    assert "[CREDIT_CARD]" in out
    assert "4111" not in out


def test_long_digit_sequence_failing_luhn_not_redacted():
    # 16 digits but fails Luhn — tracking ID, not a card.
    out = redact("Tracking number: 1234567890123456")
    assert "[CREDIT_CARD]" not in out


def test_ipv4_redacted():
    out = redact("Connection from 192.168.1.42 rejected.")
    assert "[IP]" in out
    assert "192.168.1.42" not in out


def test_ipv6_redacted():
    out = redact("Address 2001:0db8:85a3:0000:0000:8a2e:0370:7334 attempted login.")
    assert "[IP]" in out
    assert "2001:0db8" not in out


@pytest.mark.parametrize(
    "key",
    [
        "sk-1234567890abcdefghijklmnopqrstu",
        "sk-ant-api03-aaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "AKIAIOSFODNN7EXAMPLE",
        "gsk_abc123def456ghi789jkl012",
    ],
)
def test_api_key_shapes_redacted(key):
    out = redact(f"Token leaked: {key} please rotate.")
    assert "[API_KEY]" in out
    assert key not in out


def test_truncation_to_preview_max():
    long = "a" * 1000 + " contact me at safe@example.test"
    out = redact(long)
    assert out is not None
    assert len(out) <= PREVIEW_MAX + len("[EMAIL]")
    # The email was past index 500, so it should be cut off — preview must not contain it.
    assert "[EMAIL]" not in out


def test_provider_names_not_falsely_flagged():
    out = redact("Tested with openai gpt-4o and anthropic claude-3-5-sonnet.")
    assert "openai" in out
    assert "gpt-4o" in out
    assert "claude-3-5-sonnet" in out


def test_allowlist_contains_provider_names():
    assert is_allowlisted("openai")
    assert is_allowlisted("OpenAI")
    assert is_allowlisted("anthropic")
    assert not is_allowlisted("john.doe@example.test")


def test_multiple_categories_in_one_string():
    raw = (
        "User john@example.test on 192.168.0.1 called +1-415-555-0100 "
        "with card 4111111111111111 and API key sk-livedeadbeef00112233445566778899"
    )
    out = redact(raw)
    assert "[EMAIL]" in out
    assert "[IP]" in out
    assert "[PHONE]" in out
    assert "[CREDIT_CARD]" in out
    assert "[API_KEY]" in out
    # No raw PII survives
    assert "john@example.test" not in out
    assert "192.168.0.1" not in out
    assert "4111111111111111" not in out
    assert "sk-livedeadbeef" not in out


def test_presidio_layer_invoked_when_available():
    """Verify the Presidio code path runs when the backend reports available."""
    backend = redact_module._presidio
    fake_analyzer = MagicMock()
    fake_result = MagicMock()
    fake_result.start = 0
    fake_result.end = 4
    fake_result.entity_type = "PERSON"
    fake_analyzer.analyze.return_value = [fake_result]

    # Bypass the disable_presidio fixture for this test.
    object.__setattr__(backend, "_unavailable", False)
    object.__setattr__(backend, "_analyzer", fake_analyzer)
    try:
        out = redact("Jane sent the report.")
        assert "[PERSON]" in out
        assert "Jane" not in out
    finally:
        object.__setattr__(backend, "_analyzer", None)
        object.__setattr__(backend, "_unavailable", True)
