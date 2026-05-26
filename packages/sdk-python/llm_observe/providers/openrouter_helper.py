"""OpenRouter helpers.

OpenRouter is OpenAI-API-compatible — calling code uses the OpenAI SDK with
``base_url='https://openrouter.ai/api/v1'`` and an OpenRouter API key. The
response shape mirrors OpenAI's chat.completion / chat.completion.chunk, so
the openai_helper extractors work as-is for non-streaming responses. This
module exposes:

- ``openrouter_client_kwargs()``: returns the kwargs to pass to ``AsyncOpenAI``
  so it points at OpenRouter with the recommended HTTP-Referer / X-Title
  headers for leaderboard attribution.
- ``extract_openrouter_usage(response)``: thin wrapper that delegates to
  ``extract_openai_usage`` but also pulls OpenRouter-specific metadata
  (the underlying provider slug, generation id) into the metadata dict.
"""

from __future__ import annotations

import os
from typing import Any

from llm_observe.providers.openai_helper import extract_openai_usage

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def openrouter_client_kwargs() -> dict[str, Any]:
    """Kwargs for ``AsyncOpenAI(...)`` that target OpenRouter.

    Reads ``OPENROUTER_API_KEY`` and the optional ``OPENROUTER_HTTP_REFERER`` /
    ``OPENROUTER_X_TITLE`` for the leaderboard headers.
    """
    headers: dict[str, str] = {}
    referer = os.getenv("OPENROUTER_HTTP_REFERER")
    title = os.getenv("OPENROUTER_X_TITLE")
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-Title"] = title

    kwargs: dict[str, Any] = {
        "base_url": OPENROUTER_BASE_URL,
        "api_key": os.environ.get("OPENROUTER_API_KEY", ""),
    }
    if headers:
        kwargs["default_headers"] = headers
    return kwargs


def extract_openrouter_usage(response: Any) -> dict[str, Any]:
    """Extract usage + OpenRouter-specific metadata from a non-streaming response."""
    base = extract_openai_usage(response)
    metadata = dict(base.get("metadata") or {})
    # OpenRouter passes a `provider` field at the top level naming the
    # upstream that actually served the request. Useful for cross-provider
    # latency comparisons in the dashboard.
    upstream = getattr(response, "provider", None) or (
        response.get("provider") if isinstance(response, dict) else None
    )
    if upstream:
        metadata["openrouter_upstream"] = upstream
    generation_id = getattr(response, "id", None) or (
        response.get("id") if isinstance(response, dict) else None
    )
    if generation_id:
        metadata["openrouter_generation_id"] = generation_id
    base["metadata"] = metadata
    return base
