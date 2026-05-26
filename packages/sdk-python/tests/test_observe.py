from __future__ import annotations

import asyncio
from uuid import uuid4

import llm_observe.context as observe_module
import pytest
from llm_observe import observe
from llm_observe.transport import Transport


@pytest.fixture
def captured(monkeypatch):
    """Replace the default transport with one that just records payloads."""
    captured_payloads: list = []

    class _Capture(Transport):
        def __init__(self):
            self._buffer = []  # not actually used
            self._closed = False
            self._config = None  # type: ignore[assignment]

        def enqueue(self, payload):  # type: ignore[override]
            captured_payloads.append(payload)

    monkeypatch.setattr(observe_module, "get_transport", lambda: _Capture())
    return captured_payloads


async def test_success_path_records_usage_and_cost(captured):
    session_id = uuid4()
    async with observe(provider="openai", model="gpt-4o-mini", session_id=session_id) as ctx:
        ctx.set_usage(input_tokens=10, output_tokens=20)
        ctx.set_preview(input_text="hello", output_text="hi there")

    assert len(captured) == 1
    payload = captured[0]
    assert payload.session_id == session_id
    assert payload.request_status.value == "SUCCESS"
    assert payload.input_tokens == 10
    assert payload.output_tokens == 20
    assert payload.input_preview == "hello"
    assert payload.output_preview == "hi there"
    assert payload.total_cost > 0
    assert payload.latency_ms >= 0


async def test_exception_is_classified_as_error_and_reraised(captured):
    with pytest.raises(RuntimeError):
        async with observe(provider="openai", model="gpt-4o-mini", session_id=uuid4()):
            raise RuntimeError("boom")

    assert len(captured) == 1
    assert captured[0].request_status.value == "ERROR"
    assert "RuntimeError" in (captured[0].error_stack or "")


async def test_cancellation_is_classified(captured):
    with pytest.raises(asyncio.CancelledError):
        async with observe(provider="openai", model="gpt-4o-mini", session_id=uuid4()):
            raise asyncio.CancelledError()

    assert len(captured) == 1
    assert captured[0].request_status.value == "CANCELLED"


async def test_preview_truncates_to_500_chars(captured):
    long_text = "x" * 1000
    async with observe(provider="openai", model="gpt-4o-mini", session_id=uuid4()) as ctx:
        ctx.set_preview(output_text=long_text)

    assert len(captured[0].output_preview or "") == 500


async def test_unknown_model_flags_cost_missing(captured):
    async with observe(provider="openai", model="not-a-real-model", session_id=uuid4()) as ctx:
        ctx.set_usage(100, 100)

    payload = captured[0]
    assert payload.total_cost == 0.0
    assert payload.metadata.get("cost_missing") is True
