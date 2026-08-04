/// Everything the task detail sheet derives from LIVE provider state, as one
/// immutable value.
///
/// Extracted out of `task_detail_sheet.dart`, verbatim rules and comments. It
/// was ~45 lines of derivation sitting at the top of `build`, ahead of the
/// widget tree, in a file on this project's 800-line ceiling — and the reason
/// each line is written the way it is (saved-vs-working sub-tasks, the "money
/// sign" sourcing) mattered far more than its position in a build method.
///
/// The distinction it encodes, and the one thing not to lose: the sheet's own
/// `_subtasks` is an UNSAVED working list, whereas comments and expenses can
/// only ever reference sub-tasks the server already knows about. Everything
/// here is therefore keyed off the SAVED task, never off the working copy.
library;

import '../../models/expense.dart';
import '../../models/project.dart';
import '../../models/task.dart';
import '../../providers/budgets_provider.dart';
import 'task_expense_rollup.dart';

class TaskDetailLiveData {
  const TaskDetailLiveData({
    required this.task,
    required this.commentCounts,
    required this.expenseTotals,
    required this.expenseCurrency,
    required this.spent,
    required this.currency,
    required this.project,
    required this.savedSubtaskIds,
  });

  /// The freshly-watched task. Comments are IMMEDIATE (they write straight
  /// through the notifier, not save-gated like the sheet's fields), so the
  /// snapshot the sheet was opened with goes stale the moment one is added.
  final Task task;

  /// Comment count per SAVED sub-task id. A locally-added sub-task doesn't
  /// exist server-side yet, so it deliberately gets no entry: opening a
  /// comment thread for one would replay `comment_add` against an unknown
  /// `subtask_id` (a definitive 400 the outbox then drains, silently erasing
  /// the comment). [SubtaskEditor] hides the 💬 affordance for ids missing
  /// from this map.
  final Map<String, int> commentCounts;

  /// Expense total per SAVED sub-task id — "the money sign". Sourced from the
  /// same saved ids as [commentCounts] for exactly the same reason.
  final Map<String, double> expenseTotals;
  final String expenseCurrency;

  /// The task-level spend rollup and its currency.
  final double spent;
  final String currency;

  /// The destination project for money added from this sheet. Null = the task
  /// has no project (or names one that can't be resolved), which disables the
  /// "Add expense" action with a stated reason.
  final Project? project;

  /// The ids an expense's or comment's `subtask_id` may legally point at.
  final Set<String> savedSubtaskIds;

  /// Derive everything from the current provider state.
  ///
  /// [fallback] is the sheet's opening snapshot, used when the task is no
  /// longer in the live list (deleted elsewhere while the sheet was open) so
  /// the sheet renders something coherent instead of throwing.
  factory TaskDetailLiveData.resolve({
    required Task fallback,
    required List<Task> liveTasks,
    required BudgetsState budgets,
    required List<Project> pickerProjects,
    required String? category,
  }) {
    final live =
        liveTasks.where((t) => t.id == fallback.id).firstOrNull ?? fallback;
    final List<Expense> expenses = budgets.expenses;
    return TaskDetailLiveData(
      task: live,
      commentCounts: {
        for (final s in live.subtasks)
          s.id: live.taskComments.where((c) => c.subtaskId == s.id).length,
      },
      expenseTotals: subtaskExpenseTotals(expenses, fallback.id),
      expenseCurrency: subtaskExpenseCurrency(expenses, fallback.id),
      spent: taskExpenseTotal(expenses, fallback.id),
      currency: taskExpenseCurrency(expenses, fallback.id),
      project: resolveTaskProject(
        name: category,
        preferred: pickerProjects,
        fallback: budgets.projects,
      ),
      savedSubtaskIds: {for (final s in live.subtasks) s.id},
    );
  }
}
