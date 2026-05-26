from llm_observe.pricing import compute_cost


def test_known_model_returns_positive_cost():
    cost, missing = compute_cost("openai", "gpt-4o-mini", input_tokens=1000, output_tokens=500)
    assert missing is False
    # 1000 in @ $0.00015 + 500 out @ $0.0006 = 0.00015 + 0.0003 = 0.00045
    assert cost == round(0.00015 + 0.0003, 8)


def test_unknown_model_flags_missing():
    cost, missing = compute_cost("openai", "gpt-9-future", input_tokens=100, output_tokens=200)
    assert cost == 0.0
    assert missing is True


def test_zero_tokens_zero_cost():
    cost, missing = compute_cost("anthropic", "claude-3-5-sonnet-latest", 0, 0)
    assert cost == 0.0
    assert missing is False
