"""Tests for Fix F — stuck detector catches memory-tool / search_tools loops.

Real bug from 2026-05-13 18:16: the brain called ``recall_memories`` 13× in a
row in the background lane. Each call had a different query string so each
result was different. The existing result-similarity bypass in
``detect_tool_loop`` therefore returned None and the loop ran until
MAX_ITERATIONS.
"""
from __future__ import annotations

import pytest

from lazyclaw.runtime.stuck_detector import (
    DEFAULT_LOOP_LIMITS,
    _LOOKUP_TOOLS_BYPASS_SIMILARITY,
    detect_tool_loop,
)


def test_default_limits_include_recall_memories_and_search_tools() -> None:
    """Guard against accidental removal of the lookup-tool caps."""
    assert DEFAULT_LOOP_LIMITS.get("recall_memories") == 4
    assert DEFAULT_LOOP_LIMITS.get("search_tools") == 4


def test_bypass_set_includes_the_lookup_tools() -> None:
    assert "recall_memories" in _LOOKUP_TOOLS_BYPASS_SIMILARITY
    assert "search_tools" in _LOOKUP_TOOLS_BYPASS_SIMILARITY


# ── The bug we're closing ────────────────────────────────────────────


def test_recall_memories_loop_fires_even_with_varying_results() -> None:
    # Each call returns DIFFERENT content (varying queries returning
    # varying-but-useless lists). Before Fix F the similarity bypass let
    # this loop forever. After Fix F it hits the limit=4 cap.
    history = ["recall_memories"] * 4
    results = [
        "note A about cats",
        "note B about dogs",
        "note C about birds",
        "note D about fish",
    ]
    signal = detect_tool_loop(history, results=results)
    assert signal is not None
    assert signal.reason == "loop"
    assert signal.tool_name == "recall_memories"


def test_recall_memories_under_limit_does_not_fire() -> None:
    # 3 calls < limit=4 → no signal yet.
    history = ["recall_memories"] * 3
    results = ["a", "b", "c"]
    assert detect_tool_loop(history, results=results) is None


def test_search_tools_loop_fires_with_varying_results() -> None:
    history = ["search_tools"] * 4
    results = ["found a", "found b", "found c", "found d"]
    signal = detect_tool_loop(history, results=results)
    assert signal is not None
    assert signal.tool_name == "search_tools"


# ── Regression: batch-progress tools must still be allowed ───────────


def test_email_get_distinct_emails_is_not_stuck() -> None:
    # Reading 10 different emails — each returns genuinely distinct
    # content (similarity < 0.85). Bypass should still suppress the
    # signal for NON-lookup tools, so email_get under email_ prefix
    # isn't punished for legitimate batch reads. Crafted so consecutive
    # pairs are dissimilar enough to trip the < 0.85 ratio.
    history = ["email_get"] * 10
    results = [
        "From: alice@a.com\nSubject: Quarterly revenue review meeting tomorrow at 9am" + "x" * 200,
        "From: bob@b.org\nSubject: Re: lunch plans\n\nYep see you at noon!",
        "From: carla@c.io\nSubject: " + "z" * 1000,
        "Marketing newsletter October — discount codes inside, exclusive deals",
        "Calendar invite: 2026-05-14 09:00 strategic offsite all-day session",
        "Shipping notification — package delivered Friday at 14:32 by courier",
        "GitHub: new PR #2847 opened by user mona-lisa requesting code review",
        "Slack digest: 18 unread mentions across #engineering and #product",
        "From: hr@company.com\nSubject: Benefits enrollment closing this Friday",
        "From: support@vendor.com\nSubject: Invoice #INV-2026-7791 due 15 days",
    ]
    # email_get under _BATCH_OP_PREFIXES gets limit=10
    assert detect_tool_loop(history, results=results) is None


def test_browser_loop_with_varying_pages_still_bypassed() -> None:
    # Browser called 5× with different URLs returning genuinely
    # different pages — existing "batch progress" pattern, must NOT
    # fire. Pages must be dissimilar enough to trip <0.85 similarity.
    history = ["browser"] * 5
    results = [
        "<html><body><h1>Inbox</h1><div>" + "a" * 500 + "</div></body></html>",
        "<html><body><h1>Profile</h1><table>" + "b" * 1500 + "</table></body></html>",
        "Job listing: Senior Python Developer\nLocation: Remote\nSalary: $120k+",
        "<html><head><title>Settings</title></head><body>" + "z" * 300 + "</body></html>",
        "Search results page 3 of 47 — " + "q" * 800,
    ]
    assert detect_tool_loop(history, results=results) is None


def test_recall_memories_with_identical_results_also_fires() -> None:
    # Sanity: when results are also identical (true stuck loop), the
    # detector fires via the original code path too.
    history = ["recall_memories"] * 4
    results = ["no results"] * 4
    signal = detect_tool_loop(history, results=results)
    assert signal is not None
    assert signal.reason == "loop"


def test_recall_memories_history_without_results_argument_still_caps() -> None:
    # When caller doesn't pass results (older signature), default limit
    # of 4 still applies for recall_memories.
    history = ["recall_memories"] * 4
    assert detect_tool_loop(history) is not None


# ── Interleaved calls don't false-fire ───────────────────────────────


def test_recall_memories_interleaved_with_other_tools_does_not_fire() -> None:
    # The detector looks at the trailing run. If recall_memories is
    # interleaved with other tools, no run of 4 in a row → no signal.
    history = [
        "recall_memories", "browser", "recall_memories",
        "save_memory", "recall_memories",
    ]
    results = ["a", "b", "c", "d", "e"]
    assert detect_tool_loop(history, results=results) is None
