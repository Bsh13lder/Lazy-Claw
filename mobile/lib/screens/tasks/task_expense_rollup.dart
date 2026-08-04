/// Pure expense rollups for a task and its sub-tasks.
///
/// Extracted out of `task_detail_sheet.dart` (already past this project's
/// file-size ceiling) rather than grown into it. `task_detail_sheet.dart`
/// re-exports this library, so the existing
/// `import '.../task_detail_sheet.dart'` call sites that reach for
/// [subtaskExpenseTotals] keep resolving unchanged.
///
/// Everything here is a pure function over an already-loaded expense list —
/// no Flutter, no provider, no I/O — so the money math is unit-testable
/// without a widget tree (see `test/screens/tasks/task_expense_rollup_test.dart`).
library;

import '../../core/project_resolver.dart';
import '../../models/expense.dart';
import '../../models/project.dart';

// ── Project resolution (feeds every money action on the sheet) ────────────────

/// The task's destination project row, resolved from its `category` NAME
/// (tasks store a project name, expenses need the project's id).
///
/// Prefers [preferred] — the picker list the caller handed in — and falls back
/// to [fallback] (the budgets cache) because some call sites open the detail
/// sheet without surfacing project editing at all (`projects: const []`): the
/// task still HAS a project there, and "add expense" must not be dead just
/// because the picker is. Returns null when the name is blank or doesn't
/// resolve to exactly one project ([resolveProjectMatch] never guesses on an
/// ambiguous match).
Project? resolveTaskProject({
  required String? name,
  required List<Project> preferred,
  required List<Project> fallback,
}) {
  if (name == null || name.trim().isEmpty) return null;
  return resolveProjectMatch(name, preferred.isNotEmpty ? preferred : fallback);
}

// ── Task level (feeds the BUDGET control's "spent" figure) ────────────────────

/// The total LIVE (non-void) money recorded against [taskId].
///
/// Deliberately INCLUDES expenses pinned to one of the task's sub-tasks: money
/// spent on a sub-task is money spent on the task, and the allocated-vs-spent
/// readout would under-report (and never trip its own over-budget signal) if
/// it only counted the un-pinned ones. This is exactly the axis on which it
/// differs from [subtaskExpenseTotals], which is per-ROW and therefore must
/// NOT fold in task-level money.
double taskExpenseTotal(List<Expense> expenses, String taskId) {
  var total = 0.0;
  for (final e in expenses) {
    if (e.taskId != taskId || e.isVoid) continue;
    total += e.amount;
  }
  return total;
}

/// The currency to render [taskExpenseTotal] in — the first live task-linked
/// expense's, defaulting to 'USD' when the task has none yet.
///
/// One currency per task is safe because a task has at most one project and
/// `BudgetsNotifier.addExpense` inherits the project's currency, so every
/// expense on a task shares it.
String taskExpenseCurrency(List<Expense> expenses, String taskId) {
  for (final e in expenses) {
    if (e.taskId == taskId && !e.isVoid) return e.currency;
  }
  return 'USD';
}

// ── Sub-task level (feeds SubtaskEditor's per-row money chip) ─────────────────

/// The per-sub-task expense totals for [taskId], keyed by sub-task id — the
/// data [SubtaskEditor.expenseTotals] renders as its money chip. Only LIVE
/// (non-void) expenses actually linked to a sub-task count: a demoted
/// expense (its sub-task was deleted server-side, per the backend's
/// demote-never-delete cascade) has `subtaskId == null` and correctly rolls
/// up only at the task level, not here.
Map<String, double> subtaskExpenseTotals(
  List<Expense> expenses,
  String taskId,
) {
  final out = <String, double>{};
  for (final e in expenses) {
    final sid = e.subtaskId;
    if (e.taskId != taskId || sid == null || e.isVoid) continue;
    out[sid] = (out[sid] ?? 0) + e.amount;
  }
  return out;
}

/// The currency to render [subtaskExpenseTotals] in — taken from the first
/// live task-linked, sub-task-linked expense found (a task's expenses all
/// belong to the task's one project, hence share one currency), defaulting
/// to 'USD' when the task has no such expense yet.
String subtaskExpenseCurrency(List<Expense> expenses, String taskId) {
  for (final e in expenses) {
    if (e.taskId == taskId && e.subtaskId != null && !e.isVoid) {
      return e.currency;
    }
  }
  return 'USD';
}
