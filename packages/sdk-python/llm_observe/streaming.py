"""Stream proxies that yield chunks to the caller while updating the
ObservationContext in-flight.

Design constraints:
- TTFT is recorded on the first chunk that carries content, not the first
  network event (some providers send empty role/start chunks first).
- The wrapper's try/finally must flush ctx state even if the consumer
  abandons the generator mid-stream (cancellation). Python guarantees
  finally clauses run on aclose()/close() of an async generator.
- We never swallow exceptions; observe() classifies them into status.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from llm_observe.context import ObservationContext
from llm_observe.tokenization import estimate_openai_tokens

logger = logging.getLogger("llm_observe.streaming")


def _extract_openai_delta(chunk: Any) -> tuple[str, dict[str, Any] | None]:
    """Return (text_delta, usage_or_None) for an OpenAI streaming chunk."""
    choices = getattr(chunk, "choices", None) or []
    text = ""
    if choices:
        delta = getattr(choices[0], "delta", None)
        if delta is not None:
            text = getattr(delta, "content", None) or ""

    usage = getattr(chunk, "usage", None)
    usage_dict: dict[str, Any] | None = None
    if usage is not None:
        if isinstance(usage, dict):
            usage_dict = {
                "input_tokens": int(usage.get("prompt_tokens", 0)),
                "output_tokens": int(usage.get("completion_tokens", 0)),
            }
        else:
            usage_dict = {
                "input_tokens": int(getattr(usage, "prompt_tokens", 0)),
                "output_tokens": int(getattr(usage, "completion_tokens", 0)),
            }
    return text, usage_dict


async def stream_openai(
    stream: AsyncIterator[Any],
    ctx: ObservationContext,
    *,
    prompt_text: str | None = None,
) -> AsyncIterator[Any]:
    """Wrap an OpenAI async chat-completions stream.

    Yields the original chunks unchanged so callers can keep using their existing
    SSE rendering logic. ctx is updated *incrementally* — the output preview
    grows with each chunk, so even if the consumer raises mid-iteration (and
    observe()'s finally runs before this generator's finally), ctx already
    reflects the partial state.

    Final-chunk usage and tokenization fallback run in finally, which still
    executes on clean completion and on errors *inside* the stream. Cancellation
    initiated by the consumer leaves ctx with partial preview but zero tokens
    until tiktoken can run — that's the documented loss profile.
    """
    accumulated: list[str] = []
    saw_final_usage = False

    try:
        async for chunk in stream:
            text, usage = _extract_openai_delta(chunk)
            if text:
                ctx.mark_first_token()
                accumulated.append(text)
                # Update preview live so partial state survives consumer cancellation.
                ctx.set_preview(output_text="".join(accumulated))
            if usage is not None:
                saw_final_usage = True
                ctx.set_usage(
                    input_tokens=usage["input_tokens"],
                    output_tokens=usage["output_tokens"],
                )
            yield chunk
    finally:
        if not saw_final_usage:
            output_text = "".join(accumulated)
            in_tokens = estimate_openai_tokens(prompt_text, ctx.model) if prompt_text else None
            out_tokens = estimate_openai_tokens(output_text, ctx.model)
            ctx.set_usage(
                input_tokens=in_tokens or 0,
                output_tokens=out_tokens or 0,
            )
            ctx.merge_metadata({"cost_estimated": True})


def _extract_anthropic_event(event: Any) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    """Return (text_delta, metadata_patch, usage_or_None) for an Anthropic event.

    Anthropic event types we care about:
    - message_start: carries usage.input_tokens
    - content_block_delta with delta.type=text_delta: text chunk
    - message_delta: usage.output_tokens, stop_reason
    - message_stop: terminal
    """
    event_type = getattr(event, "type", None) or (
        event.get("type") if isinstance(event, dict) else None
    )
    text = ""
    metadata_patch: dict[str, Any] = {}
    usage_patch: dict[str, Any] | None = None

    if event_type == "message_start":
        message = getattr(event, "message", None) or (
            event.get("message") if isinstance(event, dict) else None
        )
        if message is not None:
            usage = getattr(message, "usage", None) or (
                message.get("usage") if isinstance(message, dict) else None
            )
            if usage is not None:
                input_tokens = (
                    usage.get("input_tokens", 0)
                    if isinstance(usage, dict)
                    else getattr(usage, "input_tokens", 0)
                )
                usage_patch = {"input_tokens": int(input_tokens)}
    elif event_type == "content_block_delta":
        delta = getattr(event, "delta", None) or (
            event.get("delta") if isinstance(event, dict) else None
        )
        if delta is not None:
            delta_type = getattr(delta, "type", None) or (
                delta.get("type") if isinstance(delta, dict) else None
            )
            if delta_type == "text_delta":
                text = getattr(delta, "text", None) or (
                    delta.get("text") if isinstance(delta, dict) else ""
                )
                text = text or ""
    elif event_type == "message_delta":
        usage = getattr(event, "usage", None) or (
            event.get("usage") if isinstance(event, dict) else None
        )
        if usage is not None:
            output_tokens = (
                usage.get("output_tokens", 0)
                if isinstance(usage, dict)
                else getattr(usage, "output_tokens", 0)
            )
            usage_patch = {"output_tokens": int(output_tokens)}
        delta = getattr(event, "delta", None) or (
            event.get("delta") if isinstance(event, dict) else None
        )
        if delta is not None:
            stop_reason = getattr(delta, "stop_reason", None) or (
                delta.get("stop_reason") if isinstance(delta, dict) else None
            )
            if stop_reason is not None:
                metadata_patch["stop_reason"] = stop_reason

    return text, metadata_patch, usage_patch


async def stream_anthropic(
    stream: AsyncIterator[Any],
    ctx: ObservationContext,
) -> AsyncIterator[Any]:
    """Wrap an Anthropic async messages stream.

    Like stream_openai, updates ctx incrementally — input_tokens land on
    message_start, output_tokens trickle through message_delta, and the
    preview is rebuilt on every text_delta. Cancellation preserves
    whatever state arrived before the cancel point.
    """
    accumulated: list[str] = []
    input_tokens = 0
    output_tokens = 0

    async for event in stream:
        text, metadata_patch, usage = _extract_anthropic_event(event)
        if text:
            ctx.mark_first_token()
            accumulated.append(text)
            ctx.set_preview(output_text="".join(accumulated))
        if metadata_patch:
            ctx.merge_metadata(metadata_patch)
        if usage is not None:
            if "input_tokens" in usage:
                input_tokens = usage["input_tokens"]
            if "output_tokens" in usage:
                output_tokens = usage["output_tokens"]
            ctx.set_usage(input_tokens=input_tokens, output_tokens=output_tokens)
        yield event
