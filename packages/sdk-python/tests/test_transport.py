from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from llm_observe.config import ObserveConfig
from llm_observe.transport import Transport
from shared_schema import InferenceLog, Provider, RequestStatus


def _payload() -> InferenceLog:
    return InferenceLog(
        log_id=uuid4(),
        session_id=uuid4(),
        provider=Provider.OPENAI,
        model="gpt-4o-mini",
        request_status=RequestStatus.SUCCESS,
        latency_ms=42.0,
        input_tokens=1,
        output_tokens=1,
        total_cost=0.0,
        timestamp=datetime.now(UTC),
    )


async def test_buffer_evicts_oldest_on_overflow(monkeypatch):
    # Point at an unreachable URL so the drain loop's _send fails fast (no network).
    cfg = ObserveConfig(
        ingestion_url="http://127.0.0.1:1", buffer_size=2, timeout_seconds=0.1
    )
    t = Transport(cfg)
    payloads = [_payload() for _ in range(5)]

    # Make _send always claim failure so the buffer stays full.
    async def always_fail(_payload):
        await asyncio.sleep(0.01)
        return False

    monkeypatch.setattr(t, "_send", always_fail)

    for p in payloads:
        t.enqueue(p)

    # Buffer should never exceed maxlen.
    assert len(t._buffer) == 2
    await t.aclose()


async def test_send_returns_true_on_202(monkeypatch):
    cfg = ObserveConfig(ingestion_url="http://test/ingest", buffer_size=10, timeout_seconds=0.5)
    t = Transport(cfg)

    async def fake_post(url, json):  # noqa: ARG001
        return httpx.Response(202, request=httpx.Request("POST", url))

    t._client = httpx.AsyncClient()
    monkeypatch.setattr(t._client, "post", fake_post)
    ok = await t._send(_payload())
    assert ok is True
    await t._client.aclose()


async def test_send_returns_false_on_5xx(monkeypatch):
    cfg = ObserveConfig(ingestion_url="http://test/ingest", buffer_size=10)
    t = Transport(cfg)

    async def fake_post(url, json):  # noqa: ARG001
        return httpx.Response(503, request=httpx.Request("POST", url))

    t._client = httpx.AsyncClient()
    monkeypatch.setattr(t._client, "post", fake_post)
    ok = await t._send(_payload())
    assert ok is False
    await t._client.aclose()


async def test_send_drops_on_permanent_4xx(monkeypatch):
    cfg = ObserveConfig(ingestion_url="http://test/ingest", buffer_size=10)
    t = Transport(cfg)

    async def fake_post(url, json):  # noqa: ARG001
        return httpx.Response(422, request=httpx.Request("POST", url), text="invalid")

    t._client = httpx.AsyncClient()
    monkeypatch.setattr(t._client, "post", fake_post)
    # Returning True here means "stop retrying" — payload is dropped from the buffer.
    ok = await t._send(_payload())
    assert ok is True
    await t._client.aclose()
