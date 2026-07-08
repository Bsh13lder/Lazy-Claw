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


def test_full_mode_pure_defaults_untouched(caplog) -> None:
    """FULL mode ships brain == fallback == claude-sonnet-4-6 with zero
    user pins. That's a deliberate shipped default, not a config bug —
    the guard must not fire and must not warn."""
    from lazyclaw.llm.model_registry import get_mode_models

    s = EcoSettings(mode="full")
    with caplog.at_level("WARNING"):
        models = _resolve(s)

    assert models == get_mode_models("full")
    assert not any("self-fallback" in r.message for r in caplog.records)


def test_collision_via_brain_pin_alone_still_guarded() -> None:
    """A user pin still counts as "contributing" to the collision even
    when it's the ONLY pin set — the guard must still fire and rewrite
    the fallback to something distinct from the (pinned) brain."""
    from lazyclaw.llm.model_registry import get_mode_models

    default_fallback = get_mode_models("full")["fallback"]
    s = EcoSettings(mode="full", full_brain_model=default_fallback)
    models = _resolve(s)

    assert models["brain"] == default_fallback
    assert models["fallback"] != default_fallback


def test_no_distinct_fallback_available_leaves_as_is(caplog) -> None:
    """When brain pin == fallback pin == the mode default fallback ==
    the safe constant (HYBRID ships Haiku as its default fallback, which
    IS _SAFE_FALLBACK_MODEL), there is no distinct fallback to rewrite
    to. Leave resolved as-is and don't emit the "using X instead"
    warning — it would be a lie."""
    s = EcoSettings(
        mode="hybrid",
        hybrid_brain_model="claude-haiku-4-5-20251001",
        hybrid_fallback_model="claude-haiku-4-5-20251001",
    )
    with caplog.at_level("WARNING"):
        models = _resolve(s)

    assert models["fallback"] == models["brain"] == "claude-haiku-4-5-20251001"
    assert not any("using" in r.message and "instead" in r.message for r in caplog.records)
