from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from api.db import Base  # noqa: E402  (Alembic needs late import of Base)
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

if config.config_file_name:
    fileConfig(config.config_file_name)

# Allow DATABASE_URL env override at migration time (CI / docker compose use this).
db_url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
assert db_url is not None
config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = db_url
    connectable = async_engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_offline() -> None:
    context.configure(url=db_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
