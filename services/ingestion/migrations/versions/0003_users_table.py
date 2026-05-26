"""users table + sessions.user_id FK

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-22

Adds a users table for the chat UI's username-only "claim any name" identity
model. The existing sessions.user_id column already existed as a nullable UUID
column; this migration adds the FK reference and a NOT NULL constraint going
forward (existing rows from before this migration may have user_id=NULL and
are left as-is — those are pre-user-table telemetry probes).
"""

from __future__ import annotations

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            username VARCHAR(64) NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_users_username ON users (LOWER(username))")
    # The sessions table already has a user_id UUID column from 0001. Add the
    # FK and supporting index. We leave it nullable to keep pre-existing rows
    # valid; the application layer enforces non-null on new sessions.
    op.execute(
        "ALTER TABLE sessions ADD CONSTRAINT fk_sessions_user "
        "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE sessions DROP CONSTRAINT IF EXISTS fk_sessions_user")
    op.execute("DROP TABLE IF EXISTS users")
