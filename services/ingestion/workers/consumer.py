"""Redis Streams consumer that bulk-inserts InferenceLog rows into Postgres.

Lifecycle:
1. ensure_group() creates the consumer group on the stream (idempotent).
2. run_forever() loops calling XREADGROUP with COUNT=batch_size and BLOCK=batch_ms.
3. Each batch is parsed, redacted (Phase 4), and COPY-inserted in one transaction.
4. XACK on success. On failure, increment a per-message retry counter; after
   worker_max_retries push to DLQ and XACK so we don't loop forever.

The worker is intentionally a separate process from the FastAPI app — its
heavy work (Postgres COPY, future Presidio NER) must not affect ingestion p99.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from typing import Any

import asyncpg
from api.settings import get_settings
from redis.asyncio import Redis
from redis.exceptions import ResponseError
from shared_schema import InferenceLog

from workers.redact import redact

logger = logging.getLogger("worker")

# Retry tracking is held in-memory on each worker. If the worker restarts, the
# count resets; that's acceptable because XACK only happens after either success
# or DLQ push, so messages are never silently lost.
_retry_counts: dict[str, int] = {}


async def ensure_group(redis: Redis, stream: str, group: str) -> None:
    try:
        await redis.xgroup_create(name=stream, groupname=group, id="$", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" in str(exc):
            return
        raise


def _row_from_payload(payload: InferenceLog) -> tuple[Any, ...]:
    """Return the tuple of column values in the order COPY expects.

    Previews are redacted here, in the worker — never on the FastAPI hot path
    and never written to disk in their raw form.
    """
    return (
        payload.log_id,
        payload.message_id,
        payload.session_id,
        payload.provider.value,
        payload.model,
        payload.request_status.value,
        payload.latency_ms,
        payload.ttft_ms,
        payload.input_tokens,
        payload.output_tokens,
        payload.total_cost,
        payload.error_stack,
        redact(payload.input_preview),
        redact(payload.output_preview),
        payload.timestamp,
        json.dumps(payload.metadata),
    )


_COPY_COLUMNS = (
    "id",
    "message_id",
    "session_id",
    "provider",
    "model",
    "status",
    "latency_ms",
    "ttft_ms",
    "input_tokens",
    "output_tokens",
    "total_cost",
    "error_stack",
    "input_preview",
    "output_preview",
    "created_at",
    "metadata",
)


def _asyncpg_dsn(database_url: str) -> str:
    """asyncpg.connect() doesn't accept the sqlalchemy '+asyncpg' suffix."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://")


async def bulk_insert(rows: list[tuple[Any, ...]], pool: asyncpg.Pool) -> None:
    if not rows:
        return
    async with pool.acquire() as conn:
        # ON CONFLICT can't be expressed via COPY, so the idempotency story
        # depends on the SDK never reusing log_ids (which it doesn't — UUIDv4).
        # If a duplicate ever does arrive it raises UniqueViolation and the
        # batch goes to DLQ.
        await conn.copy_records_to_table(
            "inference_logs",
            records=rows,
            columns=_COPY_COLUMNS,
        )


async def _process_message(
    redis: Redis,
    pool: asyncpg.Pool,
    message_id: str,
    fields: dict[str, str],
) -> InferenceLog | None:
    raw = fields.get("payload")
    if raw is None:
        logger.warning("message %s missing 'payload' field", message_id)
        return None
    try:
        return InferenceLog.model_validate_json(raw)
    except Exception:
        logger.exception("failed to parse payload %s", message_id)
        return None


async def _send_to_dlq(redis: Redis, dlq: str, message_id: str, fields: dict[str, str]) -> None:
    await redis.xadd(dlq, {**fields, "origin_id": message_id})


async def reclaim_pending(
    redis: Redis,
    *,
    stream: str,
    group: str,
    consumer: str,
    min_idle_ms: int,
    count: int = 100,
) -> list[tuple[str, dict[str, str]]]:
    """Reclaim pending messages that have been idle longer than ``min_idle_ms``.

    Uses XAUTOCLAIM (Redis 6.2+) which combines the XPENDING + XCLAIM pattern
    into a single command. Returns claimed messages as [(id, fields), ...].

    This does a single XAUTOCLAIM call per invocation, capped at ``count``.
    The caller is expected to invoke periodically (see settings.worker_reclaim_period_s)
    so pagination happens across calls rather than in a tight loop — that
    avoids unbounded work and plays nice with the cursor semantics.

    Idempotency: XAUTOCLAIM only returns messages whose idle exceeds the
    threshold AND aren't already claimed by another consumer in this call.
    The idle timer resets on claim, so two workers won't race on the same row.
    """
    result = await redis.xautoclaim(
        name=stream,
        groupname=group,
        consumername=consumer,
        min_idle_time=min_idle_ms,
        start_id="0-0",
        count=count,
    )
    # redis-py returns [next_cursor, [(id, fields), ...], deleted_ids]
    _next_cursor, messages, _deleted = result
    claimed: list[tuple[str, dict[str, str]]] = []
    for entry in messages:
        if isinstance(entry, tuple):
            claimed.append((entry[0], entry[1]))
        else:
            claimed.append((entry["message_id"], entry["fields"]))
    return claimed


async def drain_once(
    redis: Redis,
    pool: asyncpg.Pool,
    *,
    stream: str,
    group: str,
    consumer: str,
    dlq: str,
    batch_size: int,
    block_ms: int,
    max_retries: int,
    reclaimed: list[tuple[str, dict[str, str]]] | None = None,
) -> int:
    """Read and process at most one batch. Returns count of rows inserted.

    If ``reclaimed`` is provided (from a prior ``reclaim_pending`` call), those
    messages are processed instead of doing a fresh XREADGROUP. This lets the
    caller alternate between draining fresh work and reprocessing stuck
    messages without complicating the inner loop.
    """
    if reclaimed:
        entries = list(reclaimed)
    else:
        response = await redis.xreadgroup(
            groupname=group,
            consumername=consumer,
            streams={stream: ">"},
            count=batch_size,
            block=block_ms,
        )
        if not response:
            return 0

        # response = [(stream_name, [(message_id, {fields}), ...])]
        _, entries = response[0]
        if not entries:
            return 0

    rows: list[tuple[Any, ...]] = []
    parsed_ids: list[str] = []
    for message_id, fields in entries:
        payload = await _process_message(redis, pool, message_id, fields)
        if payload is None:
            await _send_to_dlq(redis, dlq, message_id, fields)
            await redis.xack(stream, group, message_id)
            continue
        rows.append(_row_from_payload(payload))
        parsed_ids.append(message_id)

    if not rows:
        return 0

    try:
        await bulk_insert(rows, pool)
    except Exception:
        logger.exception("bulk insert failed; per-message retry path")
        # Per-message retry. Try them one by one; failures cross the retry
        # threshold get DLQ'd.
        for message_id, row, (_orig_id, fields) in zip(
            parsed_ids, rows, entries, strict=False
        ):
            try:
                await bulk_insert([row], pool)
                await redis.xack(stream, group, message_id)
                _retry_counts.pop(message_id, None)
            except Exception:
                attempts = _retry_counts.get(message_id, 0) + 1
                _retry_counts[message_id] = attempts
                logger.warning(
                    "insert failure attempt %d for message %s", attempts, message_id
                )
                if attempts >= max_retries:
                    await _send_to_dlq(redis, dlq, message_id, fields)
                    await redis.xack(stream, group, message_id)
                    _retry_counts.pop(message_id, None)
        return 0

    for message_id in parsed_ids:
        await redis.xack(stream, group, message_id)
        _retry_counts.pop(message_id, None)
    return len(rows)


async def run_forever(consumer_name: str = "worker-1") -> None:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    pool = await asyncpg.create_pool(_asyncpg_dsn(settings.database_url), min_size=1, max_size=8)
    assert pool is not None
    await ensure_group(redis, settings.stream_name, settings.consumer_group)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    logger.info("worker %s starting; stream=%s group=%s", consumer_name, settings.stream_name, settings.consumer_group)

    reclaim_period = settings.worker_reclaim_period_s
    reclaim_min_idle = settings.worker_reclaim_min_idle_ms
    last_reclaim = 0.0

    try:
        while not stop.is_set():
            now = loop.time()
            # Periodically scan for pending messages that have been idle too
            # long — either crashed workers or our own bulk-insert failures
            # that left rows in PEL.
            reclaimed: list[tuple[str, dict[str, str]]] | None = None
            if now - last_reclaim >= reclaim_period:
                last_reclaim = now
                try:
                    reclaimed = await reclaim_pending(
                        redis,
                        stream=settings.stream_name,
                        group=settings.consumer_group,
                        consumer=consumer_name,
                        min_idle_ms=reclaim_min_idle,
                    )
                    if reclaimed:
                        logger.info("reclaimed %d pending messages", len(reclaimed))
                except Exception:
                    logger.exception("reclaim_pending failed; skipping this cycle")
                    reclaimed = None

            inserted = await drain_once(
                redis,
                pool,
                stream=settings.stream_name,
                group=settings.consumer_group,
                consumer=consumer_name,
                dlq=settings.dlq_stream_name,
                batch_size=settings.worker_batch_size,
                block_ms=settings.worker_batch_ms,
                max_retries=settings.worker_max_retries,
                reclaimed=reclaimed,
            )
            if inserted:
                logger.debug("inserted %d rows", inserted)
    finally:
        await pool.close()
        await redis.aclose()


if __name__ == "__main__":
    import os
    asyncio.run(run_forever(os.getenv("WORKER_NAME", "worker-1")))
