"""Per-model token pricing (cost per 1K tokens).

Used by TUI dashboard for real-time cost tracking.
If a model isn't listed, falls back to gpt-5-mini rates.

refresh_rates() fetches latest prices from Anthropic and OpenAI.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MODEL_COSTS: dict[str, dict[str, float]] = {
    # OpenAI
    "gpt-5-mini": {"input": 0.00015, "output": 0.0006},
    # MiniMax — Token Plan subscription (flat monthly fee, no per-token billing).
    # Plus $20/mo = 4,500 M2.7 req / 5h. Max $50/mo = 15,000 / 5h.
    # Keys cover both capitalizations the registry uses (MiniMax-M2.7 vs minimax-m2.5).
    "MiniMax-M2.7": {"input": 0.0, "output": 0.0},
    "MiniMax-Text-01": {"input": 0.0, "output": 0.0},
    "minimax-m2.7": {"input": 0.0, "output": 0.0},
    "minimax-m2.7-highspeed": {"input": 0.0, "output": 0.0},
    "minimax-m2.5": {"input": 0.0, "output": 0.0},
    "minimax-m2.5-highspeed": {"input": 0.0, "output": 0.0},
    "gpt-5": {"input": 0.005, "output": 0.015},
    "gpt-4.1-mini": {"input": 0.0004, "output": 0.0016},
    "gpt-4.1": {"input": 0.002, "output": 0.008},
    # Anthropic — https://docs.anthropic.com/en/docs/about-claude/pricing
    "claude-sonnet-4-6": {"input": 0.003, "output": 0.015},
    "claude-haiku-4-5-20251001": {"input": 0.001, "output": 0.005},
    "claude-opus-4-6": {"input": 0.005, "output": 0.025},
    # Claude Code CLI — subscription, no per-token cost
    "claude-cli": {"input": 0.0, "output": 0.0},
    "claude-cli (sonnet)": {"input": 0.0, "output": 0.0},
    "claude-cli (opus)": {"input": 0.0, "output": 0.0},
    "claude-cli (haiku)": {"input": 0.0, "output": 0.0},
    # Local Ollama models — $0 always
    "lazyclaw-e2b": {"input": 0.0, "output": 0.0},
    "lazyclaw-e4b": {"input": 0.0, "output": 0.0},
    "gemma4:e2b": {"input": 0.0, "output": 0.0},
    "gemma4:e4b": {"input": 0.0, "output": 0.0},
}

_FALLBACK = MODEL_COSTS["gpt-5-mini"]


def calculate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Return USD cost for a single LLM call.

    MiniMax models are always $0 (subscription-based). This is a safety
    net in case MODEL_COSTS is missing a key — a missing subscription
    model would otherwise fall through to gpt-5-mini rates.
    """
    if model and model.lower().startswith("minimax"):
        return 0.0
    rates = MODEL_COSTS.get(model, _FALLBACK)
    return (tokens_in * rates["input"] + tokens_out * rates["output"]) / 1000


# Canonical pricing URLs (JSON or API endpoints)
_ANTHROPIC_PRICING_URL = "https://docs.anthropic.com/en/docs/about-claude/pricing"
_OPENAI_PRICING_URL = "https://platform.openai.com/docs/pricing"

# Map provider pricing page model names → our MODEL_COSTS keys
_ANTHROPIC_MODEL_MAP = {
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "claude-4-6-sonnet": "claude-sonnet-4-6",
    "claude-haiku-4-5": "claude-haiku-4-5-20251001",
    "claude-4-5-haiku": "claude-haiku-4-5-20251001",
    "claude-opus-4-6": "claude-opus-4-6",
    "claude-4-6-opus": "claude-opus-4-6",
}

_OPENAI_MODEL_MAP = {
    "gpt-5-mini": "gpt-5-mini",
    "gpt-5": "gpt-5",
    "gpt-4.1-mini": "gpt-4.1-mini",
    "gpt-4.1": "gpt-4.1",
}


async def refresh_rates() -> list[str]:
    """Fetch latest model rates from Anthropic and OpenAI pricing pages.

    Updates MODEL_COSTS in-place. Returns list of model names that were updated.
    Falls back gracefully — if fetch fails, existing rates are preserved.
    """
    import re

    updated: list[str] = []

    try:
        import httpx
    except ImportError:
        logger.warning("httpx not available, skipping rate refresh")
        return updated

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        # -- Anthropic --
        try:
            resp = await client.get(_ANTHROPIC_PRICING_URL)
            text = resp.text
            # Look for patterns like "$3 / MTok" or "$0.25 per million"
            # Anthropic pricing page uses per-million-token format
            for page_name, our_key in _ANTHROPIC_MODEL_MAP.items():
                if our_key not in MODEL_COSTS:
                    continue
                # Pattern: model name near input/output prices
                pattern = (
                    rf"{re.escape(page_name)}.*?"
                    rf"\$([0-9.]+)\s*(?:/\s*MTok|per\s*million).*?"
                    rf"\$([0-9.]+)\s*(?:/\s*MTok|per\s*million)"
                )
                match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
                if match:
                    input_per_m = float(match.group(1))
                    output_per_m = float(match.group(2))
                    new_input = input_per_m / 1000  # per 1K tokens
                    new_output = output_per_m / 1000
                    old = MODEL_COSTS[our_key]
                    if old["input"] != new_input or old["output"] != new_output:
                        MODEL_COSTS[our_key] = {"input": new_input, "output": new_output}
                        updated.append(our_key)
                        logger.info(
                            "Updated %s rates: $%.5f/$%.5f → $%.5f/$%.5f per 1K",
                            our_key, old["input"], old["output"], new_input, new_output,
                        )
        except Exception as exc:
            logger.warning("Failed to fetch Anthropic pricing: %s", exc)

        # -- OpenAI --
        try:
            resp = await client.get(_OPENAI_PRICING_URL)
            text = resp.text
            for page_name, our_key in _OPENAI_MODEL_MAP.items():
                if our_key not in MODEL_COSTS:
                    continue
                pattern = (
                    rf"{re.escape(page_name)}.*?"
                    rf"\$([0-9.]+)\s*(?:/\s*1[Mm]\s*(?:input|prompt)?).*?"
                    rf"\$([0-9.]+)\s*(?:/\s*1[Mm]\s*(?:output|completion)?)"
                )
                match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
                if match:
                    input_per_m = float(match.group(1))
                    output_per_m = float(match.group(2))
                    new_input = input_per_m / 1000
                    new_output = output_per_m / 1000
                    old = MODEL_COSTS[our_key]
                    if old["input"] != new_input or old["output"] != new_output:
                        MODEL_COSTS[our_key] = {"input": new_input, "output": new_output}
                        updated.append(our_key)
                        logger.info(
                            "Updated %s rates: $%.5f/$%.5f → $%.5f/$%.5f per 1K",
                            our_key, old["input"], old["output"], new_input, new_output,
                        )
        except Exception as exc:
            logger.warning("Failed to fetch OpenAI pricing: %s", exc)

    if not updated:
        logger.info("All model rates are up to date")
    return updated
