"""Endpoint-level tests for /v1/logs after the Phase 3 refactor.

The endpoint now XADDs to a Redis stream instead of writing to Postgres.
These tests use fakeredis so they don't need a live broker, and run async
so the fakeredis state stays on a single event loop.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import fakeredis.aioredis
import pytest
from api import main as main_module
from api.main import app
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def fake_redis(monkeypatch):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(main_module, "get_redis", lambda: redis)
    return redis


def _valid_payload() -> dict:
    return {
        "log_id": str(uuid4()),
        "session_id": str(uuid4()),
        "provider": "openai",
        "model": "gpt-4o-mini",
        "request_status": "SUCCESS",
        "latency_ms": 123.4,
        "input_tokens": 10,
        "output_tokens": 20,
        "total_cost": 0.0005,
        "timestamp": datetime.now(UTC).isoformat(),
        "metadata": {},
    }


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_healthz(client):
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ingest_xadds_to_stream(fake_redis, client):
    response = await client.post("/v1/logs", json=_valid_payload())
    assert response.status_code == 202
    assert await fake_redis.xlen("telemetry:v1") == 1


async def test_ingest_rejects_missing_required_field(fake_redis, client):
    payload = _valid_payload()
    del payload["latency_ms"]
    response = await client.post("/v1/logs", json=payload)
    assert response.status_code == 422


async def test_ingest_rejects_unknown_provider(fake_redis, client):
    payload = _valid_payload()
    payload["provider"] = "made-up"
    response = await client.post("/v1/logs", json=payload)
    assert response.status_code == 422


async def test_ingest_rejects_negative_latency(fake_redis, client):
    payload = _valid_payload()
    payload["latency_ms"] = -5
    response = await client.post("/v1/logs", json=payload)
    assert response.status_code == 422


async def test_ingest_returns_429_when_backlog_saturated(fake_redis, client, monkeypatch):
    await fake_redis.xadd("telemetry:v1", {"payload": "dummy"})

    from api.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "backpressure_threshold", 1)

    response = await client.post("/v1/logs", json=_valid_payload())
    assert response.status_code == 429


async def test_metrics_reports_stream_lengths(fake_redis, client):
    response = await client.get("/metrics")
    assert response.status_code == 200
    body = response.json()
    assert "stream_len" in body
    assert "dlq_len" in body
