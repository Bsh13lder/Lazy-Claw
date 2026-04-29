"""Unit tests for SkillsProfile defaults + _coerce_updates with new fields."""

from __future__ import annotations

import pytest

from lazyclaw.survival.profile import (
    DEFAULT_PROFILE,
    SkillsProfile,
    _coerce_updates,
)


def test_default_profile_seeds_python_skills():
    """First-time users get a Python-leaning starter — no 'set profile first' wall."""
    assert "python" in DEFAULT_PROFILE.skills
    assert "fastapi" in DEFAULT_PROFILE.skills
    assert "scraping" in DEFAULT_PROFILE.skills


def test_default_profile_includes_upwork_in_platforms():
    assert "upwork" in DEFAULT_PROFILE.platforms
    assert "indeed" in DEFAULT_PROFILE.platforms


def test_default_profile_tiny_gig_cap_is_100():
    assert DEFAULT_PROFILE.max_tiny_gig_budget == 100.0


def test_default_profile_default_search_sites_indeed_only():
    """Glassdoor's API has been broken upstream — default to Indeed only."""
    assert DEFAULT_PROFILE.default_search_sites == ("indeed",)


def test_skills_profile_is_immutable():
    """frozen=True means assigning a field must raise."""
    with pytest.raises((AttributeError, TypeError)):
        DEFAULT_PROFILE.skills = ("a", "b")  # type: ignore[misc]


# ---- _coerce_updates with new fields --------------------------------------

def test_coerce_max_tiny_gig_budget_strips_dollar():
    out = _coerce_updates({"max_tiny_gig_budget": "$75"})
    assert out == {"max_tiny_gig_budget": 75.0}


def test_coerce_max_tiny_gig_budget_invalid_returns_error():
    out = _coerce_updates({"max_tiny_gig_budget": "not a number"})
    assert isinstance(out, str)
    assert "max_tiny_gig_budget" in out


def test_coerce_default_results_per_search_int():
    out = _coerce_updates({"default_results_per_search": "30"})
    assert out == {"default_results_per_search": 30}


def test_coerce_default_results_per_search_float_truncates():
    out = _coerce_updates({"default_results_per_search": 25.7})
    assert out == {"default_results_per_search": 25}


def test_coerce_default_hours_old():
    out = _coerce_updates({"default_hours_old": 24})
    assert out == {"default_hours_old": 24}


def test_coerce_default_search_sites_list():
    out = _coerce_updates({"default_search_sites": ["indeed", "linkedin"]})
    assert out == {"default_search_sites": ["indeed", "linkedin"]}


def test_coerce_default_search_sites_string_wraps_to_list():
    out = _coerce_updates({"default_search_sites": "indeed"})
    assert out == {"default_search_sites": ["indeed"]}


def test_coerce_unknown_field_dropped():
    out = _coerce_updates({"skills": ["python"], "nonsense_field": 42})
    assert "nonsense_field" not in out
    assert out["skills"] == ["python"]


def test_coerce_branding_mode_passes_through():
    out = _coerce_updates({"branding_mode": "personal"})
    assert out == {"branding_mode": "personal"}


def test_coerce_max_concurrent_jobs_int():
    out = _coerce_updates({"max_concurrent_jobs": "3"})
    assert out == {"max_concurrent_jobs": 3}
