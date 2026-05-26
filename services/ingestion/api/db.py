"""SQLAlchemy async engine + ORM models for the ingestion store."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from api.settings import get_settings


class Base(DeclarativeBase):
    pass


class Session(Base):
    __tablename__ = "sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    title = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=datetime.utcnow,
        nullable=False,
        index=True,
    )


class Message(Base):
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False, index=True
    )


class InferenceLog(Base):
    """Range-partitioned by created_at (monthly). Partitions are created by
    Alembic migrations or by pg_partman in prod — see migrations/."""

    __tablename__ = "inference_logs"
    __table_args__ = (
        Index("ix_inference_logs_provider_model", "provider", "model"),
        Index("ix_inference_logs_status", "status"),
        Index("ix_inference_logs_session_id", "session_id"),
        Index("ix_inference_logs_created_at", "created_at"),
        Index(
            "ix_inference_logs_metadata",
            "metadata",
            postgresql_using="gin",
        ),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )

    id = Column(UUID(as_uuid=True), primary_key=True)
    message_id = Column(UUID(as_uuid=True), nullable=True)
    session_id = Column(UUID(as_uuid=True), nullable=False)
    provider = Column(String(50), nullable=False)
    model = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    latency_ms = Column(Float, nullable=False)
    ttft_ms = Column(Float, nullable=True)
    input_tokens = Column(Integer, nullable=False)
    output_tokens = Column(Integer, nullable=False)
    total_cost = Column(Float, nullable=False)
    error_stack = Column(Text, nullable=True)
    input_preview = Column(String(500), nullable=True)
    output_preview = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), primary_key=True, nullable=False)
    metadata_ = Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(engine(), expire_on_commit=False)
    return _session_factory
