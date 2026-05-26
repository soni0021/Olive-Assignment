"""SDK configuration. Read once at process start; never mutated at runtime."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger("llm_observe")


@dataclass(frozen=True)
class ObserveConfig:
    ingestion_url: str = field(
        default_factory=lambda: os.getenv(
            "OBSERVE_INGESTION_URL", "http://localhost:8000/v1/logs"
        )
    )
    buffer_size: int = field(
        default_factory=lambda: int(os.getenv("OBSERVE_BUFFER_SIZE", "1000"))
    )
    timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("OBSERVE_TIMEOUT_SECONDS", "2.0"))
    )
    max_retries: int = field(
        default_factory=lambda: int(os.getenv("OBSERVE_MAX_RETRIES", "3"))
    )
    enabled: bool = field(
        default_factory=lambda: os.getenv("OBSERVE_ENABLED", "true").lower() != "false"
    )


_active_config: ObserveConfig | None = None


def get_config() -> ObserveConfig:
    global _active_config
    if _active_config is None:
        _active_config = ObserveConfig()
    return _active_config


def configure(config: ObserveConfig) -> None:
    """Override config (primarily for tests and explicit setup)."""
    global _active_config
    _active_config = config
