"""Pull usage and metadata out of an Anthropic Messages response."""

from __future__ import annotations

from typing import Any


def extract_anthropic_usage(response: Any) -> dict[str, Any]:
    """Return a dict with input_tokens, output_tokens, output_text, metadata.

    Anthropic exposes `usage.input_tokens` and `usage.output_tokens` plus prompt
    caching counters (`cache_creation_input_tokens`, `cache_read_input_tokens`).
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        input_tokens = output_tokens = 0
        cache_creation = cache_read = None
    elif isinstance(usage, dict):
        input_tokens = int(usage.get("input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))
        cache_creation = usage.get("cache_creation_input_tokens")
        cache_read = usage.get("cache_read_input_tokens")
    else:
        input_tokens = int(getattr(usage, "input_tokens", 0))
        output_tokens = int(getattr(usage, "output_tokens", 0))
        cache_creation = getattr(usage, "cache_creation_input_tokens", None)
        cache_read = getattr(usage, "cache_read_input_tokens", None)

    content = getattr(response, "content", None) or []
    output_text = ""
    for block in content:
        block_type = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if block_type == "text":
            text = getattr(block, "text", None) or (
                block.get("text") if isinstance(block, dict) else ""
            )
            output_text += text or ""

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "output_text": output_text,
        "metadata": {
            "stop_reason": getattr(response, "stop_reason", None),
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
        },
    }
