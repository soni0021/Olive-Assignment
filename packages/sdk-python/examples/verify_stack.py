"""End-to-end verification that does not require any LLM API key.

POSTs a synthetic InferenceLog payload to the ingestion endpoint, then queries
Postgres directly to confirm the worker drained the Redis stream and wrote the
row. This is the test we use to confirm a `docker compose up` is healthy
without burning a real LLM provider call.

Run:
    python examples/verify_stack.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import httpx

INGESTION_URL = os.getenv("OBSERVE_INGESTION_URL", "http://localhost:8000/v1/logs")
DATABASE_URL = os.getenv(
    "VERIFY_DATABASE_URL",
    "postgresql://observe:observe@localhost:5432/observe",
)


async def post_one(log_id: str, session_id: str) -> None:
    payload = {
        "log_id": log_id,
        "session_id": session_id,
        "provider": "openai",
        "model": "gpt-4o-mini",
        "request_status": "SUCCESS",
        "latency_ms": 123.4,
        "ttft_ms": 78.9,
        "input_tokens": 12,
        "output_tokens": 34,
        "total_cost": 0.0001,
        "input_preview": "verify_stack.py probe — contact me at test@example.test",
        "output_preview": "redacted output preview",
        "timestamp": datetime.now(UTC).isoformat(),
        "metadata": {"probe": "verify_stack"},
    }
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.post(INGESTION_URL, json=payload)
        response.raise_for_status()
        print(f"  POST {INGESTION_URL} -> {response.status_code}")


async def wait_for_row(log_id: str, timeout_s: float = 30.0) -> dict | None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            row = await conn.fetchrow(
                "SELECT id, provider, model, status, latency_ms, ttft_ms, input_tokens, "
                "output_tokens, total_cost, input_preview, output_preview, metadata::text AS meta "
                "FROM inference_logs WHERE id = $1::uuid",
                log_id,
            )
            if row is not None:
                return dict(row)
            await asyncio.sleep(0.5)
        return None
    finally:
        await conn.close()


async def main() -> int:
    log_id = str(uuid4())
    session_id = str(uuid4())
    print(f"probe log_id={log_id}")

    await post_one(log_id, session_id)
    print("waiting for worker to drain the stream into postgres…")
    row = await wait_for_row(log_id)
    if row is None:
        print("FAIL: row never appeared", file=sys.stderr)
        return 1

    print("FOUND:")
    for k, v in row.items():
        print(f"  {k}: {v}")

    # Confirm the redactor ran on the preview — the email should be replaced.
    in_preview = row.get("input_preview") or ""
    if "test@example.test" in in_preview:
        print(
            "WARN: input_preview still contains raw email — redaction layer may be regex-only or disabled",
            file=sys.stderr,
        )
    elif "[EMAIL]" in in_preview:
        print("OK: PII redaction ran (regex layer caught the email).")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
