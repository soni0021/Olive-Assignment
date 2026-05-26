"""hourly materialized view + refresh policy

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-22

The view powers dashboard queries that span large time ranges. It buckets by
(hour, provider, model, status) so the dashboard can plot a 30-day window
without scanning hundreds of millions of partitioned rows.

Refresh cadence: every 5 minutes from a Kubernetes CronJob (or pg_cron). The
dashboard surface labels its numbers as "as of <view_refreshed_at>".
"""

from __future__ import annotations

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE MATERIALIZED VIEW inference_logs_hourly AS
        SELECT
            date_trunc('hour', created_at) AS hour,
            provider,
            model,
            status,
            COUNT(*) AS n,
            SUM(input_tokens) AS input_tokens,
            SUM(output_tokens) AS output_tokens,
            SUM(latency_ms) AS total_latency_ms,
            SUM(total_cost) AS total_cost,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_latency_ms,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
            percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms) AS p99_latency_ms,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY ttft_ms)
                FILTER (WHERE ttft_ms IS NOT NULL) AS p50_ttft_ms,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY ttft_ms)
                FILTER (WHERE ttft_ms IS NOT NULL) AS p95_ttft_ms,
            percentile_cont(0.99) WITHIN GROUP (ORDER BY ttft_ms)
                FILTER (WHERE ttft_ms IS NOT NULL) AS p99_ttft_ms
        FROM inference_logs
        WHERE created_at >= now() - interval '30 days'
        GROUP BY hour, provider, model, status
        WITH NO DATA
        """
    )
    # Unique index is required for REFRESH MATERIALIZED VIEW CONCURRENTLY.
    op.execute(
        "CREATE UNIQUE INDEX ix_inference_logs_hourly_pk "
        "ON inference_logs_hourly (hour, provider, model, status)"
    )
    op.execute(
        "CREATE INDEX ix_inference_logs_hourly_hour ON inference_logs_hourly (hour DESC)"
    )

    # Helper function for the cron job.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION refresh_inference_logs_hourly()
        RETURNS void AS $$
        BEGIN
            REFRESH MATERIALIZED VIEW CONCURRENTLY inference_logs_hourly;
        END;
        $$ LANGUAGE plpgsql
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS refresh_inference_logs_hourly()")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS inference_logs_hourly")
