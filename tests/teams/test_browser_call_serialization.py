"""Browser calls are SEQUENTIAL — one per assistant turn, rest skipped.

Incident 2026-08-16: Sonnet (worker) emitted 6-15 parallel `browser` tool
calls per turn while driving himap admin. The SDK dedup guard drops exact
duplicates, but the 5-7 DISTINCT survivors all executed against ONE stale
snapshot — actions 2..N ran on pages action 1 had already changed (stale
refs, blind clicks), burning ~3-4K output tokens (~$0.25) per iteration.
33 occurrences in the log. Prompt rule added to browser_specialist.md;
this pure helper is the mechanical enforcement (F1 pattern: prompt-only
rules on tool discipline don't hold).
"""

from __future__ import annotations

from lazyclaw.llm.providers.base import ToolCall
from lazyclaw.teams.runner import browser_calls_to_skip


def _tc(id_: str, name: str, **args) -> ToolCall:
    return ToolCall(id=id_, name=name, arguments=args)


class TestBrowserCallsToSkip:
    def test_single_browser_call_untouched(self):
        calls = [_tc("1", "browser", action="read")]
        assert browser_calls_to_skip(calls) == frozenset()

    def test_calls_after_the_first_action_are_skipped(self):
        # read runs, click (first action) runs, the trailing read is skipped.
        calls = [
            _tc("1", "browser", action="read"),
            _tc("2", "browser", action="click", ref="e5"),
            _tc("3", "browser", action="read"),
        ]
        assert browser_calls_to_skip(calls) == frozenset({"3"})

    def test_non_browser_calls_never_skipped(self):
        calls = [
            _tc("1", "browser", action="click", ref="e5"),   # first action → runs
            _tc("2", "web_search", query="x"),               # non-browser → never
            _tc("3", "browser", action="type", ref="e6", text="z"),  # 2nd action → skip
            _tc("4", "save_memory", content="y"),            # non-browser → never
        ]
        assert browser_calls_to_skip(calls) == frozenset({"3"})

    def test_no_browser_calls_at_all(self):
        calls = [_tc("1", "web_search", query="x")]
        assert browser_calls_to_skip(calls) == frozenset()

    def test_empty(self):
        assert browser_calls_to_skip([]) == frozenset()

    def test_read_then_action_not_starved_across_non_browser(self):
        # web_search interleaved; the read runs and the scroll (first action)
        # runs — nothing is skipped.
        calls = [
            _tc("1", "web_search", query="x"),
            _tc("2", "browser", action="read"),
            _tc("3", "browser", action="scroll"),
        ]
        assert browser_calls_to_skip(calls) == frozenset()


class TestReadBeforeActionNotStarved:
    """Regression 2026-08-16 12:34: the specialist emitted
    [snapshot, click, ...] and the guard kept ONLY the first browser call
    (the snapshot), skipping the click EVERY turn — the page never changed,
    the stuck-detector killed it, and the himap task failed repeatedly. A
    read-only call before the action must NOT starve the action."""

    def test_snapshot_then_click_runs_both(self):
        calls = [
            _tc("1", "browser", action="snapshot", task_hint="admin nav"),
            _tc("2", "browser", action="click", target="Blog posts add"),
        ]
        # Neither is skipped: the read runs, then the first (only) action runs.
        assert browser_calls_to_skip(calls) == frozenset()

    def test_reads_run_but_second_action_skipped(self):
        calls = [
            _tc("1", "browser", action="snapshot"),
            _tc("2", "browser", action="read"),
            _tc("3", "browser", action="click", ref="e5"),   # first action → runs
            _tc("4", "browser", action="type", ref="e6", text="x"),  # 2nd action → skip
            _tc("5", "browser", action="snapshot"),          # after action → skip
        ]
        assert browser_calls_to_skip(calls) == frozenset({"4", "5"})

    def test_two_actions_first_runs_second_skipped(self):
        calls = [
            _tc("1", "browser", action="click", ref="e1"),
            _tc("2", "browser", action="click", ref="e2"),
        ]
        assert browser_calls_to_skip(calls) == frozenset({"2"})

    def test_all_readonly_calls_run(self):
        calls = [
            _tc("1", "browser", action="snapshot"),
            _tc("2", "browser", action="read"),
            _tc("3", "browser", action="tabs"),
        ]
        assert browser_calls_to_skip(calls) == frozenset()
