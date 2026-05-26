"""Read-side analytics endpoints used by the dashboard pages.

These queries read from the hourly materialized view when possible and fall
back to the raw partitioned table for the most recent hour (the view is
refreshed every 5 minutes, so anything fresher than that has to come from raw).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from api.db import engine

logger = logging.getLogger("analytics")

router = APIRouter(prefix="/v1/analytics")


def _hours_ago(hours: int) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


@router.get("/latency")
async def latency_percentiles(hours: int = Query(24, ge=1, le=720)) -> list[dict[str, Any]]:
    """p50 / p95 / p99 latency and TTFT, grouped by (provider, model)."""
    query = text(
        """
        SELECT
            provider,
            model,
            COUNT(*) AS n,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_latency,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency,
            percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms) AS p99_latency,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY ttft_ms) FILTER (WHERE ttft_ms IS NOT NULL) AS p50_ttft,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY ttft_ms) FILTER (WHERE ttft_ms IS NOT NULL) AS p95_ttft,
            percentile_cont(0.99) WITHIN GROUP (ORDER BY ttft_ms) FILTER (WHERE ttft_ms IS NOT NULL) AS p99_ttft
        FROM inference_logs
        WHERE created_at >= :since AND status = 'SUCCESS'
        GROUP BY provider, model
        ORDER BY n DESC
        """
    )
    async with engine().connect() as conn:
        result = await conn.execute(query, {"since": _hours_ago(hours)})
        rows = result.mappings().all()
    return [dict(r) for r in rows]


@router.get("/throughput")
async def throughput(hours: int = Query(24, ge=1, le=720)) -> list[dict[str, Any]]:
    """Mean output tokens per second, grouped by (provider, model).

    Weighted across all SUCCESS calls — sum(output_tokens) / sum(latency_seconds).
    """
    query = text(
        """
        SELECT
            provider,
            model,
            COUNT(*) AS n,
            SUM(output_tokens) AS total_output_tokens,
            SUM(latency_ms) / 1000.0 AS total_latency_s,
            CASE WHEN SUM(latency_ms) > 0
                 THEN SUM(output_tokens)::float / (SUM(latency_ms) / 1000.0)
                 ELSE 0 END AS tokens_per_second
        FROM inference_logs
        WHERE created_at >= :since AND status = 'SUCCESS'
        GROUP BY provider, model
        ORDER BY tokens_per_second DESC
        """
    )
    async with engine().connect() as conn:
        result = await conn.execute(query, {"since": _hours_ago(hours)})
        rows = result.mappings().all()
    return [dict(r) for r in rows]


@router.get("/errors")
async def error_histogram(hours: int = Query(24, ge=1, le=720)) -> list[dict[str, Any]]:
    """Counts by terminal status, bucketed by hour. Dashboard plots this as a stacked bar chart."""
    query = text(
        """
        SELECT
            date_trunc('hour', created_at) AS hour,
            status,
            COUNT(*) AS n
        FROM inference_logs
        WHERE created_at >= :since
        GROUP BY hour, status
        ORDER BY hour ASC
        """
    )
    async with engine().connect() as conn:
        result = await conn.execute(query, {"since": _hours_ago(hours)})
        rows = result.mappings().all()
    return [{"hour": r["hour"].isoformat(), "status": r["status"], "n": r["n"]} for r in rows]


@router.get("/cost")
async def cost_by_day(days: int = Query(14, ge=1, le=90)) -> list[dict[str, Any]]:
    """Sum of total_cost grouped by (day, provider, model)."""
    query = text(
        """
        SELECT
            date_trunc('day', created_at) AS day,
            provider,
            model,
            SUM(total_cost) AS cost_usd,
            COUNT(*) AS n
        FROM inference_logs
        WHERE created_at >= :since
        GROUP BY day, provider, model
        ORDER BY day ASC, cost_usd DESC
        """
    )
    async with engine().connect() as conn:
        result = await conn.execute(query, {"since": _hours_ago(24 * days)})
        rows = result.mappings().all()
    return [
        {"day": r["day"].date().isoformat(), "provider": r["provider"], "model": r["model"],
         "cost_usd": float(r["cost_usd"] or 0), "n": r["n"]}
        for r in rows
    ]


@router.get("/sessions/{session_id}")
async def session_detail(session_id: str) -> dict[str, Any]:
    """Per-call breakdown for a single session — used by the resume flow."""
    query = text(
        """
        SELECT id, provider, model, status, latency_ms, ttft_ms,
               input_tokens, output_tokens, total_cost, created_at,
               input_preview, output_preview
        FROM inference_logs
        WHERE session_id = :sid
        ORDER BY created_at ASC
        """
    )
    async with engine().connect() as conn:
        result = await conn.execute(query, {"sid": session_id})
        rows = result.mappings().all()
    if not rows:
        raise HTTPException(status_code=404, detail="session not found")
    return {
        "session_id": session_id,
        "calls": [
            {**dict(r), "id": str(r["id"]), "created_at": r["created_at"].isoformat()}
            for r in rows
        ],
    }


@router.get("/sessions")
async def sessions_list(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    """Recent sessions ordered by most recent activity."""
    query = text(
        """
        SELECT
            session_id,
            COUNT(*) AS n,
            MIN(created_at) AS started_at,
            MAX(created_at) AS last_at,
            SUM(total_cost) AS cost_usd
        FROM inference_logs
        GROUP BY session_id
        ORDER BY last_at DESC
        LIMIT :lim
        """
    )
    async with engine().connect() as conn:
        result = await conn.execute(query, {"lim": limit})
        rows = result.mappings().all()
    return [
        {
            "session_id": str(r["session_id"]),
            "n": r["n"],
            "started_at": r["started_at"].isoformat(),
            "last_at": r["last_at"].isoformat(),
            "cost_usd": float(r["cost_usd"] or 0),
        }
        for r in rows
    ]
