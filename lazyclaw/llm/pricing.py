"""Per-model token pricing (cost per 1K tokens).

Used by TUI dashboard for real-time cost tracking.
If a model isn't listed, falls back to gpt-5-mini rates.
"""

from __future__ import annotations

MODEL_COSTS: dict[str, dict[str, float]] = {
    "gpt-5-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-5": {"input": 0.005, "output": 0.015},
    "gpt-4.1-mini": {"input": 0.0004, "output": 0.0016},
    "gpt-4.1": {"input": 0.002, "output": 0.008},
}

_FALLBACK = MODEL_COSTS["gpt-5-mini"]


def calculate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Return USD cost for a single LLM call."""
    rates = MODEL_COSTS.get(model, _FALLBACK)
    return (tokens_in * rates["input"] + tokens_out * rates["output"]) / 1000
