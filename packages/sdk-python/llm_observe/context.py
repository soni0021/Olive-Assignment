"""The public `observe` context manager.

Usage:

    async with observe(provider="openai", model="gpt-4o", session_id=sid) as ctx:
        response = await client.chat.completions.create(...)
        ctx.set_usage(
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
        )
        ctx.set_output_preview(response.choices[0].message.content)

The context manager handles:
- generating the log_id and timestamps,
- catching exceptions and mapping them to status,
- computing cost via the pricing catalog,
- pushing the payload to the transport (fire-and-forget).
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from shared_schema import InferenceLog, Provider, RequestStatus

from llm_observe.pricing import compute_cost
from llm_observe.transport import get_transport

logger = logging.getLogger("llm_observe")

_PREVIEW_MAX = 500


def _truncate(text: str | None) -> str | None:
    if text is None:
        return None
    return text[:_PREVIEW_MAX]


class ObservationContext:
    """Mutable container the caller fills in during the LLM call."""

    def __init__(self, provider: Provider, model: str, session_id: UUID) -> None:
        self.log_id = uuid4()
        self.session_id = session_id
        self.message_id: UUID | None = None
        self.provider = provider
        self.model = model
        self.input_tokens = 0
        self.output_tokens = 0
        self.input_preview: str | None = None
        self.output_preview: str | None = None
        self.metadata: dict[str, Any] = {}
        self.ttft_ms: float | None = None
        # Set by observe() at __aenter__ — streaming wrappers reference it to
        # compute TTFT from the original request initiation, not from when the
        # consumer started iterating the stream.
        self._perf_start: float = 0.0

    def set_usage(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def set_preview(self, *, input_text: str | None = None, output_text: str | None = None) -> None:
        if input_text is not None:
            self.input_preview = _truncate(input_text)
        if output_text is not None:
            self.output_preview = _truncate(output_text)

    def set_message_id(self, message_id: UUID) -> None:
        self.message_id = message_id

    def merge_metadata(self, extra: dict[str, Any]) -> None:
        self.metadata.update(extra)

    def mark_first_token(self) -> None:
        """Record TTFT on the first non-empty stream chunk. Idempotent."""
        if self.ttft_ms is None:
            self.ttft_ms = (time.perf_counter() - self._perf_start) * 1000.0


def _classify_error(exc: BaseException) -> RequestStatus:
    if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
        return RequestStatus.CANCELLED
    name = type(exc).__name__.lower()
    if "ratelimit" in name or "429" in str(exc):
        return RequestStatus.RATE_LIMITED
    return RequestStatus.ERROR


@asynccontextmanager
async def observe(
    *,
    provider: Provider | str,
    model: str,
    session_id: UUID,
) -> AsyncIterator[ObservationContext]:
    provider_enum = Provider(provider) if not isinstance(provider, Provider) else provider
    ctx = ObservationContext(provider=provider_enum, model=model, session_id=session_id)
    started_at = datetime.now(UTC)
    perf_start = time.perf_counter()
    ctx._perf_start = perf_start
    status = RequestStatus.SUCCESS
    error_stack: str | None = None

    try:
        yield ctx
    except BaseException as exc:
        status = _classify_error(exc)
        error_stack = "".join(traceback.format_exception(exc))
        raise
    finally:
        latency_ms = (time.perf_counter() - perf_start) * 1000.0
        cost, missing = compute_cost(
            provider_enum.value, ctx.model, ctx.input_tokens, ctx.output_tokens
        )
        if missing:
            ctx.metadata["cost_missing"] = True

        try:
            payload = InferenceLog(
                log_id=ctx.log_id,
                session_id=ctx.session_id,
                message_id=ctx.message_id,
                provider=provider_enum,
                model=ctx.model,
                request_status=status,
                latency_ms=latency_ms,
                ttft_ms=ctx.ttft_ms,
                input_tokens=ctx.input_tokens,
                output_tokens=ctx.output_tokens,
                total_cost=cost,
                error_stack=error_stack,
                input_preview=ctx.input_preview,
                output_preview=ctx.output_preview,
                timestamp=started_at,
                metadata=ctx.metadata,
            )
        except Exception:
            logger.exception("failed to build InferenceLog payload; dropping telemetry")
            return

        get_transport().enqueue(payload)
