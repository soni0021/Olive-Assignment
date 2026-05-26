"""Best-effort transport from SDK to ingestion endpoint.

Design constraints (see CLAUDE.md):
- Must never raise into the host application.
- Memory is bounded — the retry buffer is a deque with maxlen, oldest evicted.
- Send is fire-and-forget: the public `enqueue` returns immediately.

Acceptable loss profile: payloads are dropped on (a) buffer overflow and
(b) shutdown without flush. This is documented as the chosen durability
posture in the implementation plan.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import TYPE_CHECKING

import httpx

from llm_observe.config import ObserveConfig, get_config

if TYPE_CHECKING:
    from shared_schema import InferenceLog

logger = logging.getLogger("llm_observe.transport")


class Transport:
    """Async transport with bounded retry buffer and background drain loop."""

    def __init__(self, config: ObserveConfig | None = None) -> None:
        self._config = config or get_config()
        self._buffer: deque[InferenceLog] = deque(maxlen=self._config.buffer_size)
        self._client: httpx.AsyncClient | None = None
        self._drain_task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._closed = False

    def _ensure_started(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._config.timeout_seconds)
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = asyncio.create_task(self._drain_loop(), name="observe-drain")

    def enqueue(self, payload: InferenceLog) -> None:
        if not self._config.enabled or self._closed:
            return
        try:
            self._ensure_started()
        except RuntimeError:
            # No running event loop. Drop the payload — caller is in a sync context
            # without an event loop, which means they cannot have intended async
            # telemetry. This is rare in practice; FastAPI/Anthropic/OpenAI clients
            # all run inside loops.
            logger.debug("no event loop available; dropping payload %s", payload.log_id)
            return

        if len(self._buffer) == self._buffer.maxlen:
            evicted = self._buffer[0]
            logger.warning("buffer full; evicting log_id=%s", evicted.log_id)
        self._buffer.append(payload)
        self._wake.set()

    async def _drain_loop(self) -> None:
        backoff = 0.1
        while not self._closed:
            await self._wake.wait()
            self._wake.clear()
            while self._buffer:
                payload = self._buffer[0]
                if await self._send(payload):
                    self._buffer.popleft()
                    backoff = 0.1
                else:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
                    break

    async def _send(self, payload: InferenceLog) -> bool:
        assert self._client is not None
        try:
            response = await self._client.post(
                self._config.ingestion_url,
                json=payload.model_dump(mode="json"),
            )
        except httpx.HTTPError as exc:
            logger.warning("transport error for log_id=%s: %s", payload.log_id, exc)
            return False

        if response.status_code in (200, 202):
            return True
        if response.status_code == 429:
            logger.info("backpressure 429 for log_id=%s", payload.log_id)
            return False
        if 500 <= response.status_code < 600:
            logger.warning("server %s for log_id=%s", response.status_code, payload.log_id)
            return False
        # 4xx other than 429 is a permanent payload problem — drop it to avoid head-of-line block.
        logger.error(
            "permanent rejection %s for log_id=%s body=%s",
            response.status_code,
            payload.log_id,
            response.text[:200],
        )
        return True

    async def aclose(self) -> None:
        self._closed = True
        self._wake.set()
        if self._drain_task is not None:
            try:
                await asyncio.wait_for(self._drain_task, timeout=2.0)
            except TimeoutError:
                self._drain_task.cancel()
        if self._client is not None:
            await self._client.aclose()


_default: Transport | None = None


def get_transport() -> Transport:
    global _default
    if _default is None:
        _default = Transport()
    return _default
