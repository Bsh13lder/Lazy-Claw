"""Tests for lazyclaw.browser.cadence — per-domain human-input timing.

Covers:
- DEFAULT preserves the original human_input.py timing ranges.
- DOMAIN_OVERRIDES yields slower cadences than DEFAULT for bot-sensitive sites.
- Subdomain match (old.reddit.com → reddit.com).
- User factor overrides multiply the resolved base.
- with_factors returns a new profile (immutability), unknown / non-positive
  factor handling.
- sample_ms returns floats inside the configured range.
"""

from __future__ import annotations

import random

import pytest

from lazyclaw.browser.cadence import (
    DEFAULT,
    DOMAIN_OVERRIDES,
    CadenceProfile,
    _normalize_domain,
    _resolve_base_domain,
    get_cadence,
    sample_ms,
)


def test_default_matches_original_human_input_ranges():
    # These were the hardcoded tuples in human_input.py before Phase A.
    assert DEFAULT.click_pause_ms == (100, 400)
    assert DEFAULT.type_speed_ms == (30, 100)
    assert DEFAULT.word_boundary_ms == (80, 180)
    assert DEFAULT.micro_pause_ms == (150, 350)
    assert DEFAULT.scroll_step_ms == (30, 80)
    assert DEFAULT.post_scroll_dwell_ms == (200, 500)


def test_get_cadence_default_for_unknown_domain():
    assert get_cadence("example.com") is DEFAULT
    assert get_cadence(None) is DEFAULT
    assert get_cadence("") is DEFAULT


def test_get_cadence_uses_domain_overrides_for_reddit():
    p = get_cadence("reddit.com")
    assert p is not DEFAULT
    # Slower than default on click_pause + type_speed
    assert p.click_pause_ms[1] > DEFAULT.click_pause_ms[1]
    assert p.type_speed_ms[1] > DEFAULT.type_speed_ms[1]


def test_get_cadence_matches_subdomain():
    # old.reddit.com inherits reddit.com
    assert get_cadence("old.reddit.com").click_pause_ms == \
        get_cadence("reddit.com").click_pause_ms


def test_get_cadence_strips_www_prefix():
    assert get_cadence("www.reddit.com").click_pause_ms == \
        get_cadence("reddit.com").click_pause_ms


def test_get_cadence_normalizes_uppercase_and_whitespace():
    assert get_cadence("  Reddit.COM ").click_pause_ms == \
        get_cadence("reddit.com").click_pause_ms


# ── User factor overrides ────────────────────────────────────────────


def test_user_override_multiplies_base():
    base = get_cadence("reddit.com")
    overrides = {"reddit.com": {"click_pause_ms": 2.0}}
    p = get_cadence("reddit.com", overrides)
    assert p.click_pause_ms == (
        base.click_pause_ms[0] * 2,
        base.click_pause_ms[1] * 2,
    )


def test_user_override_via_subdomain_resolves_to_base_key():
    overrides = {"reddit.com": {"click_pause_ms": 2.0}}
    base = get_cadence("reddit.com")
    p = get_cadence("old.reddit.com", overrides)
    assert p.click_pause_ms == (
        base.click_pause_ms[0] * 2,
        base.click_pause_ms[1] * 2,
    )


def test_user_override_unknown_field_is_ignored():
    p = get_cadence("example.com", {"example.com": {"not_a_field": 5.0}})
    assert p == DEFAULT


def test_user_override_non_positive_factor_falls_back_to_one():
    p = get_cadence("example.com", {"example.com": {"click_pause_ms": -1.0}})
    assert p.click_pause_ms == DEFAULT.click_pause_ms


def test_with_factors_returns_new_instance():
    p1 = DEFAULT
    p2 = p1.with_factors({"click_pause_ms": 1.5})
    assert p1 is not p2
    assert p1.click_pause_ms == (100, 400)
    assert p2.click_pause_ms == (150, 600)


def test_with_factors_empty_returns_same_instance():
    assert DEFAULT.with_factors({}) is DEFAULT


# ── sample_ms ────────────────────────────────────────────────────────


def test_sample_ms_returns_seconds_in_range():
    rng = (200, 400)
    for _ in range(50):
        s = sample_ms(rng)
        assert 0.2 <= s <= 0.4


def test_sample_ms_handles_swapped_bounds():
    # If caller accidentally passes (max, min) — should not blow up.
    s = sample_ms((400, 200))
    assert 0.2 <= s <= 0.4


# ── helpers ──────────────────────────────────────────────────────────


def test_normalize_domain():
    assert _normalize_domain("Reddit.COM") == "reddit.com"
    assert _normalize_domain("www.reddit.com") == "reddit.com"
    assert _normalize_domain("  www.X.com  ") == "x.com"
    assert _normalize_domain(None) == ""
    assert _normalize_domain("") == ""


def test_resolve_base_domain_walks_suffix():
    assert _resolve_base_domain("reddit.com") == "reddit.com"
    assert _resolve_base_domain("a.b.c.reddit.com") == "reddit.com"
    assert _resolve_base_domain("example.com") is None


def test_domain_overrides_keys_round_trip_through_resolver():
    for key in DOMAIN_OVERRIDES:
        # Both registrable form and one extra subdomain level resolve back.
        assert _resolve_base_domain(key) == key
        assert _resolve_base_domain("foo." + key) == key


def test_cadence_profile_is_frozen():
    with pytest.raises(Exception):
        DEFAULT.click_pause_ms = (1, 2)  # type: ignore[misc]
