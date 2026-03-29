"""Per-model token pricing (cost per 1K tokens).

Used by TUI dashboard for real-time cost tracking.
If a model isn't listed, falls back to gpt-5-mini rates.
"""

from __future__ import annotations

MODEL_COSTS: dict[str, dict[str, float]] = {
    # OpenAI
    "gpt-5-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-5": {"input": 0.005, "output": 0.015},
    "gpt-4.1-mini": {"input": 0.0004, "output": 0.0016},
    "gpt-4.1": {"input": 0.002, "output": 0.008},
    # Anthropic — https://docs.anthropic.com/en/docs/about-claude/pricing
    "claude-sonnet-4-20250514": {"input": 0.003, "output": 0.015},
    "claude-sonnet-4-6-20250514": {"input": 0.003, "output": 0.015},
    "claude-haiku-4-5-20251001": {"input": 0.001, "output": 0.005},
    "claude-opus-4-20250514": {"input": 0.005, "output": 0.025},
    "claude-opus-4-5-20250410": {"input": 0.005, "output": 0.025},
    "claude-opus-4-6-20250625": {"input": 0.005, "output": 0.025},
    "claude-opus-4-6": {"input": 0.005, "output": 0.025},
    "claude-sonnet-4-6": {"input": 0.003, "output": 0.015},
    # Local MLX models — $0 always
    "mlx-community/Qwen3.5-4B-4bit": {"input": 0.0, "output": 0.0},
    "mlx-community/Qwen3.5-9B-MLX-4bit": {"input": 0.0, "output": 0.0},
    "mlx-community/Qwen3.5-9B-8bit": {"input": 0.0, "output": 0.0},
    "mlx-community/Nanbeige4.1-3B-8bit": {"input": 0.0, "output": 0.0},
    "mlx-community/Nanbeige4.1-3B-4bit": {"input": 0.0, "output": 0.0},
    # Claude Code CLI — subscription, no per-token cost
    "claude-cli": {"input": 0.0, "output": 0.0},
    "claude-cli (sonnet)": {"input": 0.0, "output": 0.0},
    "claude-cli (opus)": {"input": 0.0, "output": 0.0},
    "claude-cli (haiku)": {"input": 0.0, "output": 0.0},
    # Local Ollama models — $0 always
    "qwen3.5:9b": {"input": 0.0, "output": 0.0},
    "nanbeige4.1:3b": {"input": 0.0, "output": 0.0},
    "qwen3:0.6b": {"input": 0.0, "output": 0.0},
    "qwen3:1.7b": {"input": 0.0, "output": 0.0},
    "softw8/nanbeige4.1-3b-tools": {"input": 0.0, "output": 0.0},
}

_FALLBACK = MODEL_COSTS["gpt-5-mini"]


def calculate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Return USD cost for a single LLM call."""
    rates = MODEL_COSTS.get(model, _FALLBACK)
    return (tokens_in * rates["input"] + tokens_out * rates["output"]) / 1000
