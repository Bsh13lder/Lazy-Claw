"""Shared alias resolver — one source of truth for delegate + agent."""
from lazyclaw.teams.specialist import SpecialistConfig
from lazyclaw.teams.specialist_aliases import (
    SPECIALIST_MAP,
    resolve_specialist,
    specialist_choices,
)


def test_short_alias_resolves_to_builtin():
    spec = resolve_specialist("browser")
    assert isinstance(spec, SpecialistConfig)
    assert spec.name == "browser_specialist"


def test_full_name_resolves():
    spec = resolve_specialist("freelance_specialist")
    assert spec is not None
    assert spec.name == "freelance_specialist"


def test_unknown_returns_none():
    assert resolve_specialist("nonexistent_agent_xyz") is None


def test_choices_cover_map_and_are_unique():
    choices = specialist_choices()
    assert len(choices) == len(set(choices))
    assert set(choices) == set(SPECIALIST_MAP.keys())
    assert "browser" in choices
    assert "upwork" in choices


def test_delegate_still_uses_same_map():
    # delegate.py must not keep a private fork of the map
    from lazyclaw.skills.builtin.delegate import _SPECIALIST_MAP
    assert _SPECIALIST_MAP is SPECIALIST_MAP
