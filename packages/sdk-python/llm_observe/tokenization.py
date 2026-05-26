"""Tokenization fallbacks when a provider omits usage data.

OpenAI streams omit usage by default unless the caller passes
`stream_options={"include_usage": True}`. The streaming wrappers attempt to
read usage from the final chunk and call into here only when it's missing.

Anthropic always emits message_delta with output_tokens, so this module's
output-token estimator is OpenAI-only. For unknown providers we return None
and the wrapper sets metadata.cost_estimated=true with zero tokens.
"""

from __future__ import annotations

import logging
from functools import lru_cache

logger = logging.getLogger("llm_observe.tokenization")


@lru_cache(maxsize=8)
def _encoding_for_openai_model(model: str):
    """Return a tiktoken Encoding, falling back to o200k_base for unknown models."""
    try:
        import tiktoken
    except ImportError:
        return None

    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        # gpt-4o family uses o200k_base, gpt-3.5/4 use cl100k_base.
        # Default to o200k_base for newer models we don't recognize.
        return tiktoken.get_encoding("o200k_base")


def estimate_openai_tokens(text: str, model: str) -> int | None:
    """Best-effort token count for an OpenAI-compatible model. None if unavailable."""
    if not text:
        return 0
    enc = _encoding_for_openai_model(model)
    if enc is None:
        return None
    try:
        return len(enc.encode(text))
    except Exception:
        logger.exception("token estimation failed for model=%s", model)
        return None
