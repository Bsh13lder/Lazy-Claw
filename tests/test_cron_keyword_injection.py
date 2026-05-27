"""Cron-job keyword injection contract.

Pins three regressions found in the 2026-04-29 audit:

1. `manage_job` must be in `_TASK_TOOL_NAMES` so "delete that reminder" /
   "pause that cron" reach the manage tool without a `search_tools` round-trip.

2. The bare word "jobs" must NOT match `_SURVIVAL_KEYWORDS`. Before the fix
   it shadowed cron-job intent ("show my jobs" → gig-economy
   `search_jobs`/`apply_job`). Specific phrases ("find job", "find work",
   "freelance", "gig") still cover gig-economy intent.

3. Cron-shape phrases ("show my jobs", "cron jobs", "background jobs",
   "delete reminder") must trigger the cron-tool injection block, which
   exposes `schedule_job`/`list_jobs`/`manage_job`/`set_reminder`.
"""

from __future__ import annotations

from lazyclaw.runtime.agent import (
    _CRON_KEYWORDS,
    _CRON_TOOL_NAMES,
    _SURVIVAL_KEYWORDS,
    _TASK_TOOL_NAMES,
)


def test_manage_job_is_in_task_tool_names():
    assert "manage_job" in _TASK_TOOL_NAMES, (
        "manage_job must be auto-injected when task keywords fire — without "
        "it 'pause that cron' / 'delete that reminder' force the brain into "
        "search_tools round-trips that often fail."
    )


def test_cron_tool_set_is_complete():
    assert _CRON_TOOL_NAMES == frozenset({
        "schedule_job", "list_jobs", "manage_job", "set_reminder", "edit_job",
    })


def test_edit_job_in_cron_tool_names():
    """`edit_job` (changes a cron's schedule / instruction / name) must be
    auto-injected on edit-a-cron intent — otherwise "edit my cron job to run
    every 30 min" forces a search_tools round-trip, since manage_job only
    does pause/resume/delete, not cron-expression edits."""
    assert "edit_job" in _CRON_TOOL_NAMES


def test_bare_jobs_word_does_not_match_survival():
    """`jobs` alone is too overloaded — it must not route to gig tools."""
    assert "jobs" not in _SURVIVAL_KEYWORDS
    # Specific gig phrasing must still match
    assert "find job" in _SURVIVAL_KEYWORDS
    assert "find work" in _SURVIVAL_KEYWORDS
    assert "freelance" in _SURVIVAL_KEYWORDS
    assert "gig" in _SURVIVAL_KEYWORDS


def test_cron_shape_phrases_trigger_cron_injection():
    """Common user phrasings for inspecting/managing background jobs."""
    expected_to_match = [
        "show my jobs",
        "list jobs",
        "my jobs",
        "cron",
        "cron jobs",
        "background jobs",
        "scheduled jobs",
        "running jobs",
        "active jobs",
        "show my reminders",
        "list reminders",
        "delete reminder",
        "pause reminder",
        "edit reminder",
        "edit job",
        "manage job",
        "delete job",
        "pause job",
        "what's scheduled",
    ]
    for phrase in expected_to_match:
        # The agent lowercases the message, then does substring checks; mirror
        # that exact behaviour here so the test reflects production semantics.
        msg = f"hey lazyclaw, {phrase} please".lower()
        matched = any(kw in msg for kw in _CRON_KEYWORDS)
        assert matched, f"'{phrase}' should trigger _CRON_KEYWORDS"


def test_show_my_jobs_no_longer_matches_survival():
    """The exact phrasing that broke before the fix."""
    msg = "show my jobs".lower()
    matched_survival = any(kw in msg for kw in _SURVIVAL_KEYWORDS)
    assert not matched_survival, (
        "'show my jobs' must NOT pull gig-economy survival tools — that was "
        "the production bug."
    )
    matched_cron = any(kw in msg for kw in _CRON_KEYWORDS)
    assert matched_cron, (
        "'show my jobs' must trigger cron-job tool injection."
    )


def test_freelance_job_search_still_routes_to_survival():
    """Gig-economy intent must still work after dropping the bare word."""
    for msg in ("find me a freelance gig", "search job boards please",
                "apply for that gig", "find work on upwork"):
        msg_lower = msg.lower()
        matched = any(kw in msg_lower for kw in _SURVIVAL_KEYWORDS)
        assert matched, f"survival intent '{msg}' must still route to survival tools"
