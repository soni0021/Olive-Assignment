from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from uuid import uuid4

import llm_observe.context as observe_module
import pytest
from llm_observe import observe, stream_anthropic, stream_openai
from llm_observe.transport import Transport


@pytest.fixture
def captured(monkeypatch):
    captured_payloads: list = []

    class _Capture(Transport):
        def __init__(self):
            self._buffer = []
            self._closed = False
            self._config = None  # type: ignore[assignment]

        def enqueue(self, payload):  # type: ignore[override]
            captured_payloads.append(payload)

    monkeypatch.setattr(observe_module, "get_transport", lambda: _Capture())
    return captured_payloads


def _openai_chunk(text: str | None = None, usage: dict | None = None) -> SimpleNamespace:
    """Build a SimpleNamespace mimicking an OpenAI streaming chunk."""
    delta = SimpleNamespace(content=text) if text is not None else SimpleNamespace(content=None)
    choices = [SimpleNamespace(delta=delta, finish_reason=None)]
    chunk = SimpleNamespace(choices=choices, usage=None)
    if usage is not None:
        chunk.usage = SimpleNamespace(**usage)
    return chunk


async def _gen(chunks: list, *, raise_after: int | None = None) -> AsyncIterator:
    for i, chunk in enumerate(chunks):
        if raise_after is not None and i == raise_after:
            raise RuntimeError("provider boom")
        # Small delay so TTFT is measurable (>0)
        await asyncio.sleep(0.001)
        yield chunk


async def test_openai_stream_full_run_records_ttft_and_usage(captured):
    chunks = [
        _openai_chunk(text=""),  # empty role/start chunk
        _openai_chunk(text="Hello "),
        _openai_chunk(text="world"),
        _openai_chunk(text="!"),
        _openai_chunk(usage={"prompt_tokens": 12, "completion_tokens": 3}),
    ]
    session_id = uuid4()

    async with observe(provider="openai", model="gpt-4o-mini", session_id=session_id) as ctx:
        collected = []
        async for chunk in stream_openai(_gen(chunks), ctx, prompt_text="hi"):
            collected.append(chunk)

    assert len(collected) == 5
    payload = captured[0]
    assert payload.request_status.value == "SUCCESS"
    assert payload.ttft_ms is not None and payload.ttft_ms > 0
    assert payload.input_tokens == 12
    assert payload.output_tokens == 3
    assert payload.output_preview == "Hello world!"
    assert payload.metadata.get("cost_estimated") is not True


async def test_openai_stream_without_final_usage_falls_back_to_tiktoken(captured):
    chunks = [
        _openai_chunk(text="Hi"),
        _openai_chunk(text=" there"),
    ]
    async with observe(provider="openai", model="gpt-4o-mini", session_id=uuid4()) as ctx:
        async for _ in stream_openai(_gen(chunks), ctx, prompt_text="hello"):
            pass

    payload = captured[0]
    # tiktoken should produce a positive count for both
    assert payload.input_tokens > 0
    assert payload.output_tokens > 0
    assert payload.metadata.get("cost_estimated") is True


async def test_openai_stream_cancelled_midstream_still_flushes(captured):
    chunks = [
        _openai_chunk(text="part-one "),
        _openai_chunk(text="part-two "),
        _openai_chunk(text="part-three"),
    ]
    session_id = uuid4()

    with pytest.raises(asyncio.CancelledError):
        async with observe(provider="openai", model="gpt-4o-mini", session_id=session_id) as ctx:
            async for chunk in stream_openai(_gen(chunks), ctx, prompt_text="x"):
                if "part-two" in (chunk.choices[0].delta.content or ""):
                    raise asyncio.CancelledError()

    assert len(captured) == 1
    payload = captured[0]
    assert payload.request_status.value == "CANCELLED"
    assert payload.ttft_ms is not None
    # Output preview should contain the partial text we received before cancel
    assert payload.output_preview is not None
    assert "part-one" in payload.output_preview
    assert "part-two" in payload.output_preview
    assert "part-three" not in payload.output_preview


async def test_openai_stream_provider_error_midstream_records_error(captured):
    chunks = [
        _openai_chunk(text="some "),
        _openai_chunk(text="text "),
        _openai_chunk(text="more"),
    ]

    with pytest.raises(RuntimeError, match="provider boom"):
        async with observe(provider="openai", model="gpt-4o-mini", session_id=uuid4()) as ctx:
            async for _ in stream_openai(_gen(chunks, raise_after=2), ctx, prompt_text="x"):
                pass

    payload = captured[0]
    assert payload.request_status.value == "ERROR"
    assert "RuntimeError" in (payload.error_stack or "")
    # We still captured the partial output up to the failure point
    assert payload.output_preview is not None
    assert "some text" in payload.output_preview


async def test_anthropic_stream_full_run(captured):
    events = [
        SimpleNamespace(type="message_start", message=SimpleNamespace(usage=SimpleNamespace(input_tokens=20))),
        SimpleNamespace(
            type="content_block_delta",
            delta=SimpleNamespace(type="text_delta", text="Hi "),
        ),
        SimpleNamespace(
            type="content_block_delta",
            delta=SimpleNamespace(type="text_delta", text="there"),
        ),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
            usage=SimpleNamespace(output_tokens=5),
        ),
        SimpleNamespace(type="message_stop"),
    ]

    async with observe(
        provider="anthropic", model="claude-3-5-sonnet-latest", session_id=uuid4()
    ) as ctx:
        async for _ in stream_anthropic(_gen(events), ctx):
            pass

    payload = captured[0]
    assert payload.request_status.value == "SUCCESS"
    assert payload.ttft_ms is not None and payload.ttft_ms > 0
    assert payload.input_tokens == 20
    assert payload.output_tokens == 5
    assert payload.output_preview == "Hi there"
    assert payload.metadata.get("stop_reason") == "end_turn"
