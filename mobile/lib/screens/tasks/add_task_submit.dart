/// The ADD-TASK submit pipeline: create the task, then — only when the user
/// armed the expense chip — file a linked expense against it.
///
/// Extracted here rather than inlined in the two screens that open the sheet
/// (`tasks_screen.dart`, `home_screen.dart`) because they had already drifted
/// into duplicating the create block verbatim, and this step now SPENDS MONEY:
/// one copy of "task first, expense second, never roll the task back" is the
/// whole safety property, and two copies is one copy too many.
///
/// [submitAddTaskResult] takes its four side effects as closures instead of a
/// `WidgetRef`. That is not indirection for its own sake — it is what lets the
/// ordering and failure-isolation contract be unit-tested with a plain
/// `test()`. The `testWidgets` route would drag in a real sqflite handle, and
/// `testWidgets` runs inside FakeAsync where a real DB call never completes
/// and the test hangs (a hazard this project has already been burned by).
/// [submitAddTaskResultWithRef] is the thin Riverpod adapter the screens use.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/project_resolver.dart';
import '../../models/project.dart';
import '../../models/task.dart';
import '../../providers/budgets_provider.dart';
import '../../providers/tasks_provider.dart';
import 'add_task_result.dart';

/// What actually happened, so the caller can decide whether to say anything.
///
/// Note that FOUR of the five outcomes still mean "the task was saved". The
/// task is the primary intent; the expense is an opt-in rider on it and its
/// failure never invalidates the task.
enum AddTaskSubmitOutcome {
  /// Task created; no expense was armed.
  taskOnly,

  /// Task created and the linked expense was filed.
  taskAndExpense,

  /// Task created, but the expense write failed. The user MUST be told — a
  /// silently dropped money row is exactly the bug this enum exists for.
  expenseFailed,

  /// Task created, but the armed expense had no resolvable project to land
  /// in. Defence-in-depth: the chip is disabled without a project, so this
  /// should be unreachable from the UI.
  expenseNoProject,

  /// The task itself failed to save. The expense is deliberately skipped —
  /// an unlinked money row for a task that does not exist is worse than none.
  taskFailed;

  /// A short user-facing warning, or null when nothing needs saying.
  ///
  /// Lives on the enum so the two screens can't word the same failure two
  /// different ways. [taskFailed] returns null on purpose: the tasks notifier
  /// already surfaces its own `state.error`, and a second "expense"-flavoured
  /// message would misdirect the user about what actually broke.
  String? get expenseWarning {
    switch (this) {
      case AddTaskSubmitOutcome.expenseFailed:
        return 'Task saved, but the expense could not be attached.';
      case AddTaskSubmitOutcome.expenseNoProject:
        return 'Task saved, but the expense needs a project — add it from '
            'Money.';
      case AddTaskSubmitOutcome.taskOnly:
      case AddTaskSubmitOutcome.taskAndExpense:
      case AddTaskSubmitOutcome.taskFailed:
        return null;
    }
  }
}

/// Get-or-create a project by name (no-op for a blank name).
typedef EnsureProjectFn = Future<void> Function(String name);

/// Create the task; returns it (for its id) or null when the write failed.
typedef CreateTaskFn = Future<Task?> Function(AddTaskResult result);

/// The CURRENT project list. Called after [EnsureProjectFn] so a
/// just-created project is visible.
typedef ReadProjectsFn = List<Project> Function();

/// File an expense already linked to [taskId]. Returns false on failure.
typedef CreateLinkedExpenseFn = Future<bool> Function({
  required String projectId,
  required double amount,
  required String description,
  required String taskId,
});

/// Run the pipeline for [result]. Never throws; every failure is an outcome.
Future<AddTaskSubmitOutcome> submitAddTaskResult(
  AddTaskResult result, {
  required EnsureProjectFn ensureProject,
  required CreateTaskFn createTask,
  required ReadProjectsFn readProjects,
  required CreateLinkedExpenseFn createLinkedExpense,
}) async {
  // A PROJECT-chip pick or `/token` that doesn't match an existing project
  // auto-creates it, rather than landing in the old silent phantom-tag
  // bucket. Must happen BEFORE the project list is read below.
  final category = result.category?.trim();
  if (category != null && category.isNotEmpty) {
    await ensureProject(category);
  }

  final task = await createTask(result);
  if (task == null) return AddTaskSubmitOutcome.taskFailed;

  final amount = result.expenseAmount;
  if (amount == null) return AddTaskSubmitOutcome.taskOnly;

  // Re-read the projects HERE, not before `ensureProject` — a `#newproject`
  // token is created during this very submit, and resolving against a
  // pre-ensure snapshot would miss it and drop the expense.
  final project = category == null || category.isEmpty
      ? null
      : resolveProjectMatch(category, readProjects());
  if (project == null) return AddTaskSubmitOutcome.expenseNoProject;

  final ok = await createLinkedExpense(
    projectId: project.id,
    amount: amount,
    // The task's (already token-stripped) title doubles as the expense
    // description — "buy paint 40 eur" files "buy paint", which is what the
    // user would have typed into the expense sheet by hand.
    description: result.title,
    taskId: task.id,
  );
  return ok
      ? AddTaskSubmitOutcome.taskAndExpense
      : AddTaskSubmitOutcome.expenseFailed;
}

/// Riverpod adapter over [submitAddTaskResult] — the single entry point the
/// screens call, so neither of them re-derives the wiring.
Future<AddTaskSubmitOutcome> submitAddTaskResultWithRef(
  WidgetRef ref,
  AddTaskResult result,
) {
  return submitAddTaskResult(
    result,
    ensureProject: (name) =>
        ref.read(budgetsProvider.notifier).ensureProject(name),
    createTask: (r) => ref
        .read(tasksProvider.notifier)
        .addTask(
          r.title,
          priority: r.priority,
          dueDate: r.dueDate,
          category: r.category,
          reminderAt: r.reminderAt,
          recurring: r.recurring,
          recurUntil: r.recurUntil,
          description: r.description,
          steps: r.steps,
        ),
    readProjects: () => ref.read(budgetsProvider).projects,
    createLinkedExpense: ({
      required projectId,
      required amount,
      required description,
      required taskId,
    }) => ref
        .read(budgetsProvider.notifier)
        .addExpense(projectId, amount, description, taskId: taskId),
  );
}
