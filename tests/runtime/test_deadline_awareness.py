"""Deadline-aware workers: budget notes + progress-based auto-extension.

2026-08-18 incident: a himap research task ran browser_specialist for 63
successful steps and was hard-killed at 480s mid-step; the research_specialist
fallback died the same way at 420s. Neither ever asked for help — every
help-trigger keys on FAILURE signals, and nothing failed; they were slow
successes. And on timeout everything they gathered evaporated.

Claude Code semantics instead: a worker that is demonstrably progressing gets
more time (bounded by a hard ceiling), a worker near its deadline is told to
synthesize what it has, and the user's stop button remains the real kill.
"""

from __future__ import annotations

from lazyclaw.runtime.deadline import (
    EXTENSION_S,
    FINAL_WINDOW_S,
    MAX_EXTENSIONS,
    PROGRESS_FRESH_S,
    budget_note,
    should_extend,
)


class TestBudgetNote:
    def test_early_in_budget_no_note(self):
        assert budget_note(elapsed_s=10, budget_s=480, notes_sent=set()) is None

    def test_half_budget_paces_the_worker(self):
        notes = set()
        note = budget_note(elapsed_s=245, budget_s=480, notes_sent=notes)
        assert note is not None and "half" in note.lower()
        assert "half" in notes

    def test_half_note_sent_only_once(self):
        notes = {"half"}
        assert budget_note(elapsed_s=250, budget_s=480, notes_sent=notes) is None

    def test_final_window_forces_synthesis(self):
        notes = {"half"}
        note = budget_note(
            elapsed_s=480 - FINAL_WINDOW_S + 5, budget_s=480, notes_sent=notes,
        )
        assert note is not None
        assert "synthesize" in note.lower() or "final" in note.lower()
        assert "final" in notes

    def test_final_note_sent_only_once(self):
        notes = {"half", "final"}
        assert budget_note(elapsed_s=470, budget_s=480, notes_sent=notes) is None

    def test_no_budget_no_notes(self):
        assert budget_note(elapsed_s=9999, budget_s=0, notes_sent=set()) is None
        assert budget_note(elapsed_s=9999, budget_s=None, notes_sent=set()) is None


class TestShouldExtend:
    def test_fresh_progress_extends(self):
        assert should_extend(progress_age_s=10, extensions_used=0) is True

    def test_stale_progress_does_not_extend(self):
        assert should_extend(
            progress_age_s=PROGRESS_FRESH_S + 1, extensions_used=0,
        ) is False

    def test_extension_cap_is_hard(self):
        assert should_extend(
            progress_age_s=1, extensions_used=MAX_EXTENSIONS,
        ) is False

    def test_boundary_progress_age(self):
        assert should_extend(
            progress_age_s=PROGRESS_FRESH_S, extensions_used=0,
        ) is True

    def test_no_progress_ever_never_extends(self):
        # A worker that produced NOTHING before its deadline gets no mercy —
        # the deadline itself is the verdict (also keeps tiny test budgets
        # from being extended just because "now - start" is small).
        assert should_extend(progress_age_s=None, extensions_used=0) is False

    def test_constants_bound_total_runtime(self):
        # Worst case = budget + MAX_EXTENSIONS * EXTENSION_S. Keep the ceiling
        # meaningful: extensions must not exceed ~2x a 480s browser budget.
        assert MAX_EXTENSIONS * EXTENSION_S <= 480 * 2
        assert EXTENSION_S >= 120
        assert FINAL_WINDOW_S >= 30


class TestAllDispatchPathsAreDeadlineWired:
    """Source pins: every path that runs a worker must pass budget_s and use
    should_extend — the delegate path was missed on the first wiring pass
    (2026-08-18: fire-and-forget run_specialist with NO timeout at all;
    a form run scroll-hunted 16+ minutes unbounded)."""

    def test_agent_tool_sync_path(self):
        import inspect
        from lazyclaw.skills.builtin import agent_tool
        src = inspect.getsource(agent_tool)
        assert "budget_s=timeout_s" in src
        assert "should_extend" in src

    def test_delegate_path(self):
        import inspect
        from lazyclaw.skills.builtin import delegate
        src = inspect.getsource(delegate)
        assert "budget_s=self.DELEGATE_BUDGET_S" in src
        assert "should_extend" in src
        assert "progress_beat=_progress_beat" in src

    def test_task_runner_bg_path(self):
        import inspect
        from lazyclaw.runtime import task_runner
        src = inspect.getsource(task_runner)
        assert "should_extend" in src
