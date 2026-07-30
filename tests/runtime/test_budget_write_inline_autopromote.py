"""Budget WRITES must stay INLINE — not AUTO-PROMOTE'd to a background task.

2026-05-28 incident: "log 12 chino on clubbay" answered with "Continuing in
background" then 44s + 68s, because add_expense was not exempt from the
AUTO-PROMOTE gate (it isn't a read, so `_only_readonly_so_far` was False).
A quick single-shot budget write (one DB row + fire-and-forget LazyBrain
mirror) should finish in <1s inline. This pins the exemption set the gate
consults so the names can't silently drop back out.
"""

from __future__ import annotations

from lazyclaw.runtime.agent import _BUDGET_TOOL_NAMES, _QUICK_INLINE_BUDGET_WRITES


def test_all_budget_writes_are_inline_exempt() -> None:
    for name in (
        "add_expense",
        "set_project_budget",
        "add_project_budget",
        "add_recurring_expense",
        "set_default_expense_project",
    ):
        assert name in _QUICK_INLINE_BUDGET_WRITES, name


def test_budget_reads_are_not_in_the_write_set() -> None:
    # Reads are handled by the readonly-inspection allowlist, not here.
    assert "list_expenses" not in _QUICK_INLINE_BUDGET_WRITES
    assert "expense_report" not in _QUICK_INLINE_BUDGET_WRITES


def test_task_writes_are_not_auto_exempt() -> None:
    # add_task can spawn genuinely long-running work — it is NOT blanket
    # exempt here; its quick-reply behavior is governed by SOUL.md, not the
    # auto-promote inline exemption.
    assert "add_task" not in _QUICK_INLINE_BUDGET_WRITES


def test_write_set_is_subset_of_known_budget_tools() -> None:
    # Guard against typos — every exempt write must be a real budget tool.
    assert _QUICK_INLINE_BUDGET_WRITES <= _BUDGET_TOOL_NAMES


def test_new_budget_tools_and_keywords_wired() -> None:
    from lazyclaw.runtime import agent as agent_mod

    for name in ("move_expense", "auto_assign_inbox", "list_projects", "list_budget_topups"):
        assert name in agent_mod._BUDGET_TOOL_NAMES
    for kw in ("top up", "top-up", "topup", "inbox", "assign"):
        assert kw in agent_mod._BUDGET_KEYWORDS
    for name in ("move_expense", "auto_assign_inbox"):
        assert name in agent_mod._QUICK_INLINE_BUDGET_WRITES
    for name in ("list_projects", "list_budget_topups"):
        assert agent_mod._is_readonly_inspection(name)
