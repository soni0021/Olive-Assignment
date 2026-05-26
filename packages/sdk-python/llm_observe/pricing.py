"""Cost computation from a static YAML pricing catalog."""

from __future__ import annotations

import logging
from functools import lru_cache
from importlib import resources
from typing import TypedDict

import yaml

logger = logging.getLogger("llm_observe.pricing")


class _ModelPrice(TypedDict):
    input_per_1k: float
    output_per_1k: float


@lru_cache(maxsize=1)
def _load_catalog() -> dict[str, dict[str, _ModelPrice]]:
    raw = resources.files("llm_observe").joinpath("pricing.yaml").read_text()
    return yaml.safe_load(raw)


def compute_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> tuple[float, bool]:
    """Return (cost_usd, missing_flag).

    If the (provider, model) is unknown, returns (0.0, True). Callers should set
    metadata.cost_missing accordingly. We never raise — pricing data is too noisy
    a source of failure to be allowed to break the host.
    """
    catalog = _load_catalog()
    provider_map = catalog.get(provider)
    if provider_map is None or model not in provider_map:
        logger.warning("no pricing entry for provider=%s model=%s", provider, model)
        return 0.0, True

    rates = provider_map[model]
    cost = (input_tokens / 1000.0) * rates["input_per_1k"] + (output_tokens / 1000.0) * rates[
        "output_per_1k"
    ]
    return round(cost, 8), False
