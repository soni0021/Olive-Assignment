"""Pull usage and metadata out of an OpenAI ChatCompletion response."""

from __future__ import annotations

from typing import Any


def extract_openai_usage(response: Any) -> dict[str, Any]:
    """Return a dict with input_tokens, output_tokens, output_text, metadata.

    Accepts the typed OpenAI SDK response object. Falls back to attribute access
    via getattr so it tolerates dict responses from mocks.
    """
    usage = getattr(response, "usage", None) or {}
    if isinstance(usage, dict):
        input_tokens = int(usage.get("prompt_tokens", 0))
        output_tokens = int(usage.get("completion_tokens", 0))
    else:
        input_tokens = int(getattr(usage, "prompt_tokens", 0))
        output_tokens = int(getattr(usage, "completion_tokens", 0))

    choices = getattr(response, "choices", None) or []
    output_text = ""
    finish_reason = None
    if choices:
        first = choices[0]
        message = getattr(first, "message", None)
        if message is not None:
            output_text = getattr(message, "content", "") or ""
        finish_reason = getattr(first, "finish_reason", None)

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "output_text": output_text,
        "metadata": {
            "system_fingerprint": getattr(response, "system_fingerprint", None),
            "finish_reason": finish_reason,
        },
    }
