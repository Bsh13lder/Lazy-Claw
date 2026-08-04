/// The task detail sheet's BUDGET state, as one immutable value.
///
/// WHY it isn't four `setState` fields on the sheet: they are not independent.
/// "which editor is open", "what the readout shows" and "why the last input was
/// refused" change TOGETHER on every interaction, and the sheet — already at
/// this project's file-size ceiling — is the worst place to keep four
/// invariants in step by hand. Every transition below returns a NEW state, so
/// no interaction can leave half of it updated (e.g. a committed top-up that
/// forgot to clear a stale error).
///
/// It also gives the money rules a home that can be tested without a widget
/// tree — see `test/screens/tasks/task_budget_edit_state_test.dart`.
///
/// SCOPE: [draft] is a task's `allocated_budget` — a plaintext REAL column
/// holding its slice of the parent project's budget. A top-up here adjusts
/// that number directly; it is NOT a `budget_entries` credit row (that is the
/// PROJECT-level ledger). See `task_budget_math.dart`.
library;

import 'task_budget_control.dart';

/// Which editor is open, what the readout shows, and the pending rejection.
class TaskBudgetEditState {
  const TaskBudgetEditState({
    required this.original,
    required this.draft,
    this.editor = TaskBudgetEditor.none,
    this.error,
  });

  /// Seed from the task's SAVED allocation, with no editor open: the readout
  /// is the resting representation of that number, so opening a field beside
  /// it would show the same figure twice.
  const TaskBudgetEditState.forTask(double? allocated)
    : original = allocated,
      draft = allocated,
      editor = TaskBudgetEditor.none,
      error = null;

  /// The allocation on open (null = none). The save path's three-way baseline:
  /// it decides untouched / clear / set-to-N (see `task_detail_patch.dart`).
  /// Never changes while the sheet is open.
  final double? original;

  /// The allocation as it currently stands in the sheet — an in-progress edit
  /// or a committed top-up shows here before Save.
  final double? draft;

  /// Which inline money editor is disclosed. Exactly one at a time: two money
  /// fields open at once is how a user tops up the wrong number.
  final TaskBudgetEditor editor;

  /// The inline rejection under the allocation field (null = fine to save).
  final String? error;

  /// The user tapped the allocated figure.
  TaskBudgetEditState editing() => TaskBudgetEditState(
    original: original,
    draft: draft,
    editor: TaskBudgetEditor.allocation,
    error: error,
  );

  /// A keystroke in the allocation field. The draft survives an un-parseable
  /// one (see [nextDraftAllocation]) while the error tracks it exactly.
  TaskBudgetEditState typed(String text) => TaskBudgetEditState(
    original: original,
    draft: nextDraftAllocation(text, draft),
    editor: editor,
    error: taskAllocationError(text),
  );

  /// The user picked "Top up" from the money dropdown.
  TaskBudgetEditState toppingUp() => TaskBudgetEditState(
    original: original,
    draft: draft,
    editor: TaskBudgetEditor.topUp,
    error: error,
  );

  /// A validated top-up committed [total] as the NEW allocation. Drops any
  /// stale rejection: the field is about to be rewritten with a clean number.
  TaskBudgetEditState toppedUp(double total) =>
      TaskBudgetEditState(original: original, draft: total);

  /// A write landed: [original] — the three-way baseline — advances to what is
  /// now stored, leaving the open editor and its draft exactly as the user left
  /// them.
  ///
  /// REQUIRED by auto-save, and the reason is a silent data loss: with the
  /// baseline frozen at open, a task that started with NO allocation, was
  /// auto-saved at 300, then had its field cleared would produce
  /// `originalBudget == null` — so `buildTaskDetailPatch` reads the empty field
  /// as "nothing to clear" and the 300 stays in the database forever. The same
  /// applies to any auto-saved value the user then reverts.
  TaskBudgetEditState savedAs(double? stored) => TaskBudgetEditState(
    original: stored,
    draft: draft,
    editor: editor,
    error: error,
  );

  /// The open editor was dismissed (top-up cancelled).
  TaskBudgetEditState closed() =>
      TaskBudgetEditState(original: original, draft: draft, error: error);

  /// Save refused the typed allocation. Re-opens the allocation editor so the
  /// message lands WHERE IT IS FIXED — an `errorText` under a collapsed editor
  /// is the same silent drop that made this guard necessary.
  TaskBudgetEditState rejected(String message) => TaskBudgetEditState(
    original: original,
    draft: draft,
    editor: TaskBudgetEditor.allocation,
    error: message,
  );
}
