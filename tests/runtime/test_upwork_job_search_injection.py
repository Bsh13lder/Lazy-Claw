""""Find jobs on upwork" must route to the `search_jobs` skill, not `browser`.

2026-06-03 incident: bare "jobs" was pulled from _SURVIVAL_KEYWORDS (cron
overload), so "Find matching jobs on upwork and show me 5 of them" injected no
job-search tool. The brain fell back to the raw `browser` tool and tried to
scroll Upwork's grid, where Input.dispatchMouseEvent times out on the heavy DOM
→ the turn got AUTO-PROMOTE'd to a Telegram-bound background task. The
_UPWORK_JOB_SEARCH_RE detector fires `search_jobs` injection when "upwork"
co-occurs with a job/work/gig token (the "upwork" token disambiguates from
cron jobs).
"""

from __future__ import annotations

from lazyclaw.runtime.agent import _is_upwork_job_search


def _hit(msg: str) -> bool:
    return _is_upwork_job_search(msg.lower())


# ── the exact failing message + variants ──────────────────────────────────


def test_failing_message_now_matches() -> None:
    assert _hit("Find matching jobs on upwokr and show me 5 of them")


def test_common_upwork_job_phrasings_match() -> None:
    for msg in (
        "search jobs on upwork",
        "show me 5 upwork jobs",
        "any new upwork work for me?",
        "find me upwork gigs",
        "what projects are on upwork right now",
        "upwork contracts matching python",
        # token order flipped: job word before the platform
        "find python jobs, search upwork",
    ):
        assert _hit(msg), msg


# ── must NOT fire on cron / unrelated intents ─────────────────────────────


def test_cron_job_intent_does_not_match() -> None:
    # No "upwork" token → the detector stays silent so cron-job intent isn't
    # hijacked into a freelance search.
    for msg in (
        "list my cron jobs",
        "the background job failed",
        "schedule a job every morning",
        "show me my scheduled jobs",
    ):
        assert not _hit(msg), msg


def test_upwork_without_job_token_does_not_match() -> None:
    # "check my upwork messages" is a channel read, not a job search.
    assert not _hit("check my upwork messages")
    assert not _hit("reply to James on upwork")


def test_plain_chat_does_not_match() -> None:
    assert not _hit("hey what's up")
    assert not _hit("what's the weather today")
