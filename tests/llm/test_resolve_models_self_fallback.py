"""Tests for self-fallback rejection in _resolve_models.

With `full_fallback_model == full_brain_model == MiniMax-M3`, every
"fallback" retried the exact model that just failed. A self-fallback is
always a configuration bug.
"""
from __future__ import annotations

from lazyclaw.llm.eco_router import EcoRouter, EcoSettings


def _resolve(settings: EcoSettings) -> dict[str, str]:
    """Instantiate EcoRouter and resolve models."""
    router = EcoRouter.__new__(EcoRouter)
    return router._resolve_models(settings)


def test_self_fallback_replaced_with_mode_default() -> None:
    """When fallback == brain, replace with the mode default."""
    s = EcoSettings(
        mode="full",
        full_brain_model="MiniMax-M2.7",
        full_fallback_model="MiniMax-M2.7",
    )
    models = _resolve(s)
    assert models["brain"] == "MiniMax-M2.7"
    assert models["fallback"] != "MiniMax-M2.7"


def test_self_fallback_when_default_also_matches_uses_safe_constant() -> None:
    """When default fallback == brain, use the safe constant instead."""
    from lazyclaw.llm.model_registry import get_mode_models
    default_fb = get_mode_models("full")["fallback"]
    s = EcoSettings(
        mode="full",
        full_brain_model=default_fb,
        full_fallback_model=default_fb,
    )
    models = _resolve(s)
    assert models["fallback"] != default_fb


def test_distinct_pins_unchanged() -> None:
    """When fallback != brain, they remain as configured."""
    s = EcoSettings(
        mode="full",
        full_brain_model="MiniMax-M2.7",
        full_fallback_model="claude-haiku-4-5-20251001",
    )
    models = _resolve(s)
    assert models["fallback"] == "claude-haiku-4-5-20251001"
