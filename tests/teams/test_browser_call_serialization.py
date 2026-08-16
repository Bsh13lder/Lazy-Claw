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

    def test_second_and_later_browser_calls_skipped(self):
        calls = [
            _tc("1", "browser", action="read"),
            _tc("2", "browser", action="click", ref="e5"),
            _tc("3", "browser", action="read"),
        ]
        assert browser_calls_to_skip(calls) == frozenset({"2", "3"})

    def test_non_browser_calls_never_skipped(self):
        calls = [
            _tc("1", "browser", action="read"),
            _tc("2", "web_search", query="x"),
            _tc("3", "browser", action="click", ref="e5"),
            _tc("4", "save_memory", content="y"),
        ]
        assert browser_calls_to_skip(calls) == frozenset({"3"})

    def test_no_browser_calls_at_all(self):
        calls = [_tc("1", "web_search", query="x")]
        assert browser_calls_to_skip(calls) == frozenset()

    def test_empty(self):
        assert browser_calls_to_skip([]) == frozenset()

    def test_first_browser_call_kept_even_when_not_first_overall(self):
        calls = [
            _tc("1", "web_search", query="x"),
            _tc("2", "browser", action="read"),
            _tc("3", "browser", action="scroll"),
        ]
        assert browser_calls_to_skip(calls) == frozenset({"3"})
