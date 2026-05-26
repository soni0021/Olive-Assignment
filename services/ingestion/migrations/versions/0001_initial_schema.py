"""initial schema: sessions, messages, partitioned inference_logs

Revision ID: 0001
Revises:
Create Date: 2026-05-21

The inference_logs table is range-partitioned on created_at by month. This
migration creates the parent + 12 months of forward partitions starting from
the migration date. In production, pg_partman is configured to roll partitions
forward on a cron; this migration's set is sufficient for development through
~2027.
"""

from __future__ import annotations

from datetime import date

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def _partition_bounds(start: date) -> tuple[str, str, str]:
    """Return (name, lower, upper) bounds for the month containing start."""
    name = f"inference_logs_{start.year:04d}_{start.month:02d}"
    year, month = start.year, start.month
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    lower = f"{year:04d}-{month:02d}-01"
    upper = f"{next_year:04d}-{next_month:02d}-01"
    return name, lower, upper


def _months_forward(start: date, count: int) -> list[date]:
    months = []
    y, m = start.year, start.month
    for _ in range(count):
        months.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.execute(
        """
        CREATE TABLE sessions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NULL,
            title VARCHAR(255) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_sessions_user_id ON sessions (user_id)")
    op.execute("CREATE INDEX ix_sessions_updated_at ON sessions (updated_at DESC)")

    op.execute(
        """
        CREATE TABLE messages (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            role VARCHAR(50) NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_messages_session_id ON messages (session_id)")
    op.execute("CREATE INDEX ix_messages_created_at ON messages (created_at)")

    op.execute(
        """
        CREATE TABLE inference_logs (
            id UUID NOT NULL,
            message_id UUID NULL,
            session_id UUID NOT NULL,
            provider VARCHAR(50) NOT NULL,
            model VARCHAR(100) NOT NULL,
            status VARCHAR(50) NOT NULL,
            latency_ms DOUBLE PRECISION NOT NULL,
            ttft_ms DOUBLE PRECISION NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            total_cost DOUBLE PRECISION NOT NULL,
            error_stack TEXT NULL,
            input_preview VARCHAR(500) NULL,
            output_preview VARCHAR(500) NULL,
            created_at TIMESTAMPTZ NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
        """
    )
    op.execute(
        "CREATE INDEX ix_inference_logs_provider_model ON inference_logs (provider, model)"
    )
    op.execute("CREATE INDEX ix_inference_logs_status ON inference_logs (status)")
    op.execute("CREATE INDEX ix_inference_logs_session_id ON inference_logs (session_id)")
    op.execute("CREATE INDEX ix_inference_logs_created_at ON inference_logs (created_at)")
    op.execute(
        "CREATE INDEX ix_inference_logs_metadata ON inference_logs USING GIN (metadata)"
    )

    # 14 monthly partitions starting one month before today to cover any
    # SDK clocks that are slightly behind.
    start = date(2026, 4, 1)
    for month_start in _months_forward(start, 14):
        name, lower, upper = _partition_bounds(month_start)
        op.execute(
            f"CREATE TABLE {name} PARTITION OF inference_logs "
            f"FOR VALUES FROM ('{lower}') TO ('{upper}')"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS inference_logs CASCADE")
    op.execute("DROP TABLE IF EXISTS messages")
    op.execute("DROP TABLE IF EXISTS sessions")
