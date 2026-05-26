"""Tests for the OpenRouter provider helper."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from llm_observe.providers import extract_openrouter_usage, openrouter_client_kwargs
from llm_observe.providers.openrouter_helper import OPENROUTER_BASE_URL


def test_client_kwargs_targets_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.delenv("OPENROUTER_HTTP_REFERER", raising=False)
    monkeypatch.delenv("OPENROUTER_X_TITLE", raising=False)
    kwargs = openrouter_client_kwargs()
    assert kwargs["base_url"] == OPENROUTER_BASE_URL
    assert kwargs["api_key"] == "sk-or-test"
    assert "default_headers" not in kwargs


def test_client_kwargs_attribution_headers(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("OPENROUTER_HTTP_REFERER", "http://localhost:3000")
    monkeypatch.setenv("OPENROUTER_X_TITLE", "llm-observe")
    kwargs = openrouter_client_kwargs()
    assert kwargs["default_headers"] == {
        "HTTP-Referer": "http://localhost:3000",
        "X-Title": "llm-observe",
    }


def test_extract_openrouter_usage_carries_upstream_metadata():
    response = SimpleNamespace(
        id="gen-abc123",
        provider="Anthropic",
        usage=SimpleNamespace(prompt_tokens=42, completion_tokens=11),
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="hi there"),
                finish_reason="stop",
            )
        ],
        system_fingerprint=None,
    )
    extracted = extract_openrouter_usage(response)
    assert extracted["input_tokens"] == 42
    assert extracted["output_tokens"] == 11
    assert extracted["output_text"] == "hi there"
    assert extracted["metadata"]["openrouter_upstream"] == "Anthropic"
    assert extracted["metadata"]["openrouter_generation_id"] == "gen-abc123"


@pytest.mark.parametrize(
    "model,expect_known",
    [
        ("openai/gpt-4o-mini", True),
        ("anthropic/claude-3.5-sonnet", True),
        ("nonexistent/imaginary-model-9000", False),
    ],
)
def test_pricing_catalog_has_openrouter_entries(model, expect_known):
    from llm_observe.pricing import compute_cost

    cost, missing = compute_cost("openrouter", model, 1000, 500)
    if expect_known:
        assert missing is False
        assert cost > 0
    else:
        assert missing is True
        assert cost == 0.0
