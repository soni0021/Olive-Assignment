from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        default="postgresql+asyncpg://observe:observe@localhost:5432/observe",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    stream_name: str = Field(default="telemetry:v1", alias="REDIS_STREAM_NAME")
    dlq_stream_name: str = Field(default="telemetry:dlq", alias="REDIS_DLQ_NAME")
    consumer_group: str = Field(default="observers", alias="REDIS_CONSUMER_GROUP")
    backpressure_threshold: int = Field(
        default=100_000, alias="STREAM_BACKPRESSURE_THRESHOLD"
    )
    worker_batch_size: int = Field(default=100, alias="WORKER_BATCH_SIZE")
    worker_batch_ms: int = Field(default=50, alias="WORKER_BATCH_MS")
    worker_max_retries: int = Field(default=3, alias="WORKER_MAX_RETRIES")
    # How often each worker scans for its own stale-pending messages.
    worker_reclaim_period_s: float = Field(default=30.0, alias="WORKER_RECLAIM_PERIOD_S")
    # Idle-time threshold before a pending message is eligible for reclaim.
    # 60s is conservative; lower it if you expect bursty failure modes.
    worker_reclaim_min_idle_ms: int = Field(default=60_000, alias="WORKER_RECLAIM_MIN_IDLE_MS")
    host: str = Field(default="0.0.0.0", alias="INGESTION_HOST")  # noqa: S104
    port: int = Field(default=8000, alias="INGESTION_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")


_cached: Settings | None = None


def get_settings() -> Settings:
    global _cached
    if _cached is None:
        _cached = Settings()
    return _cached
