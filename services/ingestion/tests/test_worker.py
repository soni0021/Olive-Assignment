"""Tests for the consumer worker's drain_once path.

We use fakeredis for the stream side and a hand-rolled fake asyncpg pool that
records the records passed to copy_records_to_table.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import fakeredis.aioredis
import pytest
from shared_schema import InferenceLog, Provider, RequestStatus
from workers import consumer as worker_module
from workers.consumer import drain_once, ensure_group, reclaim_pending


class _FakeConn:
    def __init__(self, sink: list):
        self._sink = sink
        self._fail_remaining = 0

    async def copy_records_to_table(self, table, records, columns):
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            raise RuntimeError("simulated copy failure")
        self._sink.extend(records)

    def fail_next(self, n: int) -> None:
        self._fail_remaining = n


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        return None


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquire(self._conn)


def _payload() -> InferenceLog:
    return InferenceLog(
        log_id=uuid4(),
        session_id=uuid4(),
        provider=Provider.OPENAI,
        model="gpt-4o-mini",
        request_status=RequestStatus.SUCCESS,
        latency_ms=42.0,
        input_tokens=10,
        output_tokens=20,
        total_cost=0.0005,
        timestamp=datetime.now(UTC),
    )


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def reset_retry_counts():
    worker_module._retry_counts.clear()
    yield
    worker_module._retry_counts.clear()


async def test_drain_once_inserts_batch_and_acks(redis):
    sink: list = []
    conn = _FakeConn(sink)
    pool = _FakePool(conn)

    await ensure_group(redis, "telemetry:v1", "observers")
    for _ in range(3):
        await redis.xadd("telemetry:v1", {"payload": _payload().model_dump_json()})

    inserted = await drain_once(
        redis,
        pool,
        stream="telemetry:v1",
        group="observers",
        consumer="worker-1",
        dlq="telemetry:dlq",
        batch_size=10,
        block_ms=10,
        max_retries=3,
    )

    assert inserted == 3
    assert len(sink) == 3
    # Nothing acknowledged should remain pending
    pending = await redis.xpending("telemetry:v1", "observers")
    assert pending["pending"] == 0


async def test_drain_once_routes_malformed_payload_to_dlq(redis):
    sink: list = []
    pool = _FakePool(_FakeConn(sink))

    await ensure_group(redis, "telemetry:v1", "observers")
    await redis.xadd("telemetry:v1", {"payload": "not-json-at-all"})

    inserted = await drain_once(
        redis,
        pool,
        stream="telemetry:v1",
        group="observers",
        consumer="worker-1",
        dlq="telemetry:dlq",
        batch_size=10,
        block_ms=10,
        max_retries=3,
    )
    assert inserted == 0
    assert len(sink) == 0

    dlq_len = await redis.xlen("telemetry:dlq")
    assert dlq_len == 1


async def test_drain_once_dlq_after_max_retries(redis):
    sink: list = []
    # Fail both the initial bulk and the per-message retry attempts.
    conn = _FakeConn(sink)
    conn.fail_next(100)
    pool = _FakePool(conn)

    await ensure_group(redis, "telemetry:v1", "observers")
    msg = await redis.xadd("telemetry:v1", {"payload": _payload().model_dump_json()})

    # Pre-seed the retry counter to (max_retries - 1) so the single drain pass
    # crosses the threshold and DLQs. This isolates the DLQ decision logic from
    # the pending-message reclaim path (which is exercised in a separate test
    # via XCLAIM and is a Phase 4 follow-up).
    worker_module._retry_counts[msg] = 2

    await drain_once(
        redis,
        pool,
        stream="telemetry:v1",
        group="observers",
        consumer="worker-1",
        dlq="telemetry:dlq",
        batch_size=10,
        block_ms=10,
        max_retries=3,
    )

    assert await redis.xlen("telemetry:dlq") == 1
    assert msg not in worker_module._retry_counts


async def test_reclaim_pending_returns_idle_messages(redis):
    sink: list = []
    conn = _FakeConn(sink)
    pool = _FakePool(conn)

    await ensure_group(redis, "telemetry:v1", "observers")

    # Two messages delivered to worker-1, never ack'd.
    payload_a = _payload().model_dump_json()
    payload_b = _payload().model_dump_json()
    await redis.xadd("telemetry:v1", {"payload": payload_a})
    await redis.xadd("telemetry:v1", {"payload": payload_b})

    # Force them into PEL by reading without ack'ing.
    await redis.xreadgroup(
        groupname="observers",
        consumername="worker-1",
        streams={"telemetry:v1": ">"},
        count=10,
        block=10,
    )

    # min_idle_ms=0 lets us reclaim immediately for the test (in prod it's 60s+).
    reclaimed = await reclaim_pending(
        redis,
        stream="telemetry:v1",
        group="observers",
        consumer="worker-1",
        min_idle_ms=0,
    )
    assert len(reclaimed) == 2

    # Feed the reclaimed batch back through drain_once and confirm it inserts.
    inserted = await drain_once(
        redis,
        pool,
        stream="telemetry:v1",
        group="observers",
        consumer="worker-1",
        dlq="telemetry:dlq",
        batch_size=10,
        block_ms=10,
        max_retries=3,
        reclaimed=reclaimed,
    )
    assert inserted == 2
    assert len(sink) == 2
