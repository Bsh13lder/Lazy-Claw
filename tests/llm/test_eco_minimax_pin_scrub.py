"""Tests for MiniMax-M3 pin coercion to M2.7 in _parse_eco_settings.

The 2026-07-01 M3→M2.7 default revert was silently defeated by stored
per-mode pins (full_brain_model etc. = "MiniMax-M3" in users.settings.eco).
M3 is off the Token-Plan quota and has regressed tool-calling. These tests
verify the parse-time scrub.
"""
from __future__ import annotations

import json

from lazyclaw.llm.eco_router import _parse_eco_settings


def _settings(eco: dict) -> str:
    """Build a settings JSON blob from an eco dict."""
    return json.dumps({"eco": eco})


def test_m3_pins_coerced_to_m27_all_roles_and_modes() -> None:
    """All M3 role pins across modes are coerced to M2.7."""
    s = _parse_eco_settings(_settings({
        "mode": "full",
        "brain_model": "MiniMax-M3",
        "full_brain_model": "MiniMax-M3",
        "full_worker_model": "MiniMax-M3",
        "full_fallback_model": "MiniMax-M3",
        "minimax_brain_model": "MiniMax-M3",
        "hybrid_fallback_model": "MiniMax-M3",
    }))
    assert s.brain_model == "MiniMax-M2.7"
    assert s.full_brain_model == "MiniMax-M2.7"
    assert s.full_worker_model == "MiniMax-M2.7"
    assert s.full_fallback_model == "MiniMax-M2.7"
    assert s.minimax_brain_model == "MiniMax-M2.7"
    assert s.hybrid_fallback_model == "MiniMax-M2.7"


def test_m27_and_other_models_pass_through() -> None:
    """M2.7 and other valid models are not modified."""
    s = _parse_eco_settings(_settings({
        "mode": "full",
        "full_brain_model": "MiniMax-M2.7",
        "full_fallback_model": "claude-haiku-4-5-20251001",
    }))
    assert s.full_brain_model == "MiniMax-M2.7"
    assert s.full_fallback_model == "claude-haiku-4-5-20251001"


def test_none_pins_stay_none() -> None:
    """Unset pins remain None."""
    s = _parse_eco_settings(_settings({"mode": "minimax"}))
    assert s.minimax_brain_model is None
