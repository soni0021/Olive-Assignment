"""FastAPI ingestion endpoint.

Phase 3: instead of writing to Postgres directly, payloads are XADD'd to a
Redis Stream and a worker pool drains the stream. This decouples ingestion
latency from database write latency and gives us elastic burst capacity.

Backpressure: if the stream length crosses STREAM_BACKPRESSURE_THRESHOLD we
return 429 so SDKs fall back to their local retry buffer rather than piling
more work into an already-overloaded broker.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from redis.asyncio import Redis
from shared_schema import InferenceLog

from api.analytics import router as analytics_router
from api.chat_store import router as chat_store_router
from api.settings import get_settings

logging.basicConfig(level=get_settings().log_level)
logger = logging.getLogger("ingestion")

_redis: Redis | None = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if _redis is not None:
        await _redis.aclose()


app = FastAPI(title="llm-observe ingestion", version="0.5.0", lifespan=lifespan)
app.include_router(analytics_router)
app.include_router(chat_store_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/logs", status_code=status.HTTP_202_ACCEPTED)
async def ingest_log(payload: InferenceLog) -> dict[str, str]:
    settings = get_settings()
    redis = get_redis()

    try:
        backlog = await redis.xlen(settings.stream_name)
    except Exception:
        logger.exception("failed to read stream length; proceeding")
        backlog = 0

    if backlog >= settings.backpressure_threshold:
        # Tell the SDK to back off — its local buffer + retry loop will hold the
        # payload until we drain.
        raise HTTPException(status_code=429, detail="ingestion backlog saturated")

    try:
        # XADD with a single 'payload' field. We serialize once here and the
        # worker deserializes once when draining — Pydantic round-trip on both
        # ends keeps the schema contract intact.
        body = payload.model_dump_json()
        await redis.xadd(settings.stream_name, {"payload": body}, maxlen=1_000_000, approximate=True)
    except Exception:
        logger.exception("XADD failed for log_id=%s", payload.log_id)
        raise HTTPException(status_code=500, detail="broker unavailable") from None

    return {"log_id": str(payload.log_id)}


@app.get("/metrics")
async def metrics() -> dict[str, int]:
    """Crude /metrics endpoint. Phase 6 replaces this with proper Prometheus output."""
    settings = get_settings()
    redis = get_redis()
    try:
        stream_len = await redis.xlen(settings.stream_name)
        dlq_len = await redis.xlen(settings.dlq_stream_name)
    except Exception:
        logger.exception("metrics scrape failed")
        return {"stream_len": -1, "dlq_len": -1}
    return {"stream_len": stream_len, "dlq_len": dlq_len}
