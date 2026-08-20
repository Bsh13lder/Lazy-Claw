"""Specialist identical-args loop guard (2026-08-20 himap research grind).

Incident: a research_specialist re-ran the SAME five
`mcp_scraper_batch_search_google` query-sets 14 iterations in a row
(6+ repeats of literally identical arguments over 5 minutes). Every call
returned real results (4-14KB), so `detect_same_result` never tripped —
search output always differs slightly (ordering, snippets) and the
stuck-detector kept logging "results diverged — batch progress". The
BRAIN's loop has an identical-args cache + loop-guard steering for
exactly this ("You called X with these exact args N times — stop");
the specialist runner had nothing keyed on ARGUMENT identity.

Fix: `identical_call_guard` — counts exact (tool, args) repeats per run
and, from the 3rd identical call on, returns a steering message the
runner injects INSTEAD of executing the tool.
"""

from __future__ import annotations

from lazyclaw.teams.runner import identical_call_guard

_ARGS = {"queries": ["site:himap.co", "himap.co"], "num_results_per_query": 10}


def test_first_two_identical_calls_execute() -> None:
    counts: dict[str, int] = {}
    assert identical_call_guard(counts, "mcp_scraper_batch_search_google", _ARGS) is None
    assert identical_call_guard(counts, "mcp_scraper_batch_search_google", _ARGS) is None


def test_third_identical_call_steers() -> None:
    counts: dict[str, int] = {}
    for _ in range(2):
        identical_call_guard(counts, "mcp_scraper_batch_search_google", _ARGS)
    steer = identical_call_guard(counts, "mcp_scraper_batch_search_google", _ARGS)
    assert steer is not None
    assert "mcp_scraper_batch_search_google" in steer
    assert "final report" in steer.lower()


def test_every_repeat_after_the_limit_keeps_steering() -> None:
    counts: dict[str, int] = {}
    for _ in range(2):
        identical_call_guard(counts, "t", _ARGS)
    assert identical_call_guard(counts, "t", _ARGS) is not None
    assert identical_call_guard(counts, "t", _ARGS) is not None


def test_different_args_do_not_trip() -> None:
    counts: dict[str, int] = {}
    for i in range(5):
        out = identical_call_guard(counts, "t", {"q": f"query {i}"})
        assert out is None, f"varied args must never steer (call {i})"


def test_same_args_different_tools_have_separate_counters() -> None:
    counts: dict[str, int] = {}
    for _ in range(2):
        identical_call_guard(counts, "tool_a", _ARGS)
    assert identical_call_guard(counts, "tool_b", _ARGS) is None


def test_runtime_injected_keys_excluded_from_identity() -> None:
    """The runner injects `_tab_context` into browser args — a volatile
    runtime key must not defeat the identity match."""
    counts: dict[str, int] = {}
    identical_call_guard(counts, "browser", {"action": "read", "target": "u"})
    identical_call_guard(
        counts, "browser",
        {"action": "read", "target": "u", "_tab_context": {"tab": 1}},
    )
    steer = identical_call_guard(
        counts, "browser",
        {"action": "read", "target": "u", "_tab_context": {"tab": 2}},
    )
    assert steer is not None


def test_wired_into_the_runner_loop_before_execution() -> None:
    import inspect

    from lazyclaw.teams import runner as runner_mod

    src = inspect.getsource(runner_mod)
    guard_idx = src.index("identical_call_guard(", src.index("async def run_specialist"))
    exec_idx = src.index("tool_result = await executor.execute(exec_tc")
    assert guard_idx < exec_idx, (
        "the guard must run before executor.execute so a steered call "
        "is never executed"
    )
