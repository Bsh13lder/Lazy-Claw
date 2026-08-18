"""Browser action-memory scratchpad (2026-08-18 himap form incident).

Context pruning keeps only the last 2 tool results, so the specialist forgot
its OWN prior actions and clicked is_published TWICE — silently un-publishing
the post. The runner now records every MUTATING browser action and re-injects
a compact "already done" list each iteration, immune to pruning.
"""

from __future__ import annotations

from lazyclaw.teams.runner import (
    _ACTION_LOG_MARK,
    _ACTION_LOG_MAX_ENTRIES,
    render_action_log,
    summarize_browser_action,
)


class TestSummarizeBrowserAction:
    def test_click_is_recorded_with_target(self):
        out = summarize_browser_action(
            {"action": "click", "target": "Is published checkbox"},
        )
        assert out == "click target=Is published checkbox"

    def test_type_records_ref_and_text_preview(self):
        out = summarize_browser_action(
            {"action": "type", "ref": "e5", "text": "HiMap System Test Post ..."},
        )
        assert "type" in out and "ref=e5" in out
        assert 'text="HiMap System Test Post ..."' in out

    def test_chain_records_steps(self):
        out = summarize_browser_action(
            {"action": "chain", "steps": ["type e3 Title", "click Save"]},
        )
        assert "steps=[type e3 Title; click Save]" in out

    def test_readonly_actions_are_not_recorded(self):
        for action in ("read", "snapshot", "tabs", "screenshot"):
            assert summarize_browser_action({"action": action}) is None

    def test_open_is_recorded(self):
        out = summarize_browser_action(
            {"action": "open", "target": "https://himap.co/admin"},
        )
        assert out.startswith("open ")

    def test_empty_args(self):
        assert summarize_browser_action({}) is None
        assert summarize_browser_action(None) is None


class TestRenderActionLog:
    def test_contains_sentinel_and_entries(self):
        out = render_action_log(["open target=x", "click target=Save"])
        assert out.startswith(_ACTION_LOG_MARK)
        assert "1. open target=x" in out
        assert "2. click target=Save" in out
        assert "Never re-click" in out

    def test_long_logs_are_capped_with_omission_note(self):
        entries = [f"click target=btn{i}" for i in range(30)]
        out = render_action_log(entries)
        assert f"{30 - _ACTION_LOG_MAX_ENTRIES} earlier actions omitted" in out
        assert "click target=btn29" in out
        assert "click target=btn0" not in out
