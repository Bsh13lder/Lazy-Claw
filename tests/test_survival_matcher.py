"""Unit tests for survival.matcher — tiny-gig bonus, remote bonus, recency."""

from __future__ import annotations

from lazyclaw.survival.matcher import _looks_recent, score_job
from lazyclaw.survival.profile import SkillsProfile


def _profile(**kw):
    base = dict(
        skills=("python", "scraping"),
        min_hourly_rate=20.0,
        min_fixed_rate=20.0,
        max_tiny_gig_budget=100.0,
    )
    base.update(kw)
    return SkillsProfile(**base)


def _job(**kw):
    base = dict(
        id="j1",
        title="Python script for data extraction",
        description="Need a python developer to scrape some data.",
        budget="$50",
        salary="$50",
        site="upwork",
        url="https://www.upwork.com/jobs/~01abc",
    )
    base.update(kw)
    return base


# ---- tiny-gig bonus -------------------------------------------------------

def test_tiny_gig_bonus_fires_at_or_below_cap():
    job = _job(budget="$80", salary="$80")
    scored = score_job(job, _profile(max_tiny_gig_budget=100.0))
    assert "Tiny gig (≤$100)" in scored.match_reasons


def test_tiny_gig_bonus_skipped_above_cap():
    job = _job(budget="$250", salary="$250")
    scored = score_job(job, _profile(max_tiny_gig_budget=100.0))
    assert not any("Tiny gig" in r for r in scored.match_reasons)


def test_tiny_gig_bonus_skipped_for_hourly_rate():
    """Hourly rates aren't fixed-price — cap doesn't apply."""
    job = _job(budget="$45/hr", salary="$45/hr")
    scored = score_job(job, _profile(max_tiny_gig_budget=100.0))
    assert not any("Tiny gig" in r for r in scored.match_reasons)


def test_tiny_gig_bonus_skipped_when_cap_zero():
    job = _job(budget="$50")
    scored = score_job(job, _profile(max_tiny_gig_budget=0.0))
    assert not any("Tiny gig" in r for r in scored.match_reasons)


def test_tiny_gig_bonus_skipped_when_no_budget():
    job = _job(budget="", salary="")
    scored = score_job(job, _profile())
    assert not any("Tiny gig" in r for r in scored.match_reasons)


# ---- remote bonus ---------------------------------------------------------

def test_remote_bonus_when_is_remote_true():
    job = _job(is_remote=True)
    scored = score_job(job, _profile())
    assert "Remote" in scored.match_reasons


def test_no_remote_bonus_when_is_remote_false():
    job = _job(is_remote=False)
    scored = score_job(job, _profile())
    assert "Remote" not in scored.match_reasons


def test_no_remote_bonus_when_is_remote_missing():
    job = _job()  # no key at all
    scored = score_job(job, _profile())
    assert "Remote" not in scored.match_reasons


# ---- recency --------------------------------------------------------------

def test_looks_recent_today_keyword():
    assert _looks_recent("today")


def test_looks_recent_hours_ago():
    assert _looks_recent("3 hours ago")


def test_looks_recent_minutes_ago():
    assert _looks_recent("12 minutes ago")


def test_looks_recent_iso_today():
    from datetime import date
    assert _looks_recent(date.today().isoformat())


def test_looks_recent_yesterday_no_match():
    assert not _looks_recent("yesterday")


def test_looks_recent_empty_no_match():
    assert not _looks_recent("")


# ---- composition ---------------------------------------------------------

def test_score_full_stack_matches_all_bonuses():
    """A perfect tiny remote python gig hits every bonus → high score."""
    job = _job(
        title="Build a python scraper",
        description="Need python scraping work — small fixed-price job.",
        budget="$40",
        salary="$40",
        is_remote=True,
        date_posted="today",
    )
    scored = score_job(job, _profile())
    reasons = scored.match_reasons
    assert any("Skills:" in r for r in reasons)
    assert any("Tiny gig" in r for r in reasons)
    assert "Remote" in reasons
    assert "Posted today" in reasons
    assert scored.match_score > 0.7
