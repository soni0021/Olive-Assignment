"""Pydantic mirror of inference_log.schema.json.

Hand-mirrored rather than codegen'd so we can ship without a build step. If you
change the JSON Schema you MUST update this file and the TypeScript zod schema
in lockstep. CI will fail otherwise (see services/ingestion/tests/test_schema_parity.py).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Provider(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    DEEPSEEK = "deepseek"
    XAI = "xai"
    OPENROUTER = "openrouter"


class RequestStatus(StrEnum):
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"
    RATE_LIMITED = "RATE_LIMITED"


class InferenceLog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)

    log_id: UUID
    session_id: UUID
    message_id: UUID | None = None
    provider: Provider
    model: str = Field(min_length=1, max_length=100)
    request_status: RequestStatus
    latency_ms: float = Field(ge=0)
    ttft_ms: float | None = Field(default=None, ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_cost: float = Field(ge=0)
    error_stack: str | None = None
    input_preview: str | None = Field(default=None, max_length=500)
    output_preview: str | None = Field(default=None, max_length=500)
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
