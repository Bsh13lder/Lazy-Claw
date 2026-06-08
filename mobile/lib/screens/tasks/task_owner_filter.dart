import '../../models/task.dart';

/// Pure, framework-free helper backing the Tasks owner filter chip row
/// (All · Mine · AI).
///
/// Tasks carry [Task.owner]:
///   * `'user'`  — created by me (the human).
///   * `'agent'` — created by the AI on my behalf.
///
/// This lets the Tasks segment separate the two without touching the
/// underlying provider state. Kept widget-free so the filtering is trivially
/// unit-testable.

/// The owner-filter selection driving which tasks the Tasks views display.
enum TaskOwnerFilter {
  /// Every task, regardless of who created it.
  all,

  /// Only tasks I created (`owner == 'user'`).
  mine,

  /// Only tasks the AI created (`owner == 'agent'`).
  ai,
}

extension TaskOwnerFilterLabel on TaskOwnerFilter {
  /// The short chip label.
  String get label {
    switch (this) {
      case TaskOwnerFilter.all:
        return 'All';
      case TaskOwnerFilter.mine:
        return 'Mine';
      case TaskOwnerFilter.ai:
        return 'AI';
    }
  }
}

/// The owner string the server uses for AI-created tasks.
const String kAgentOwner = 'agent';

/// True when [task] was created by the AI (its [Task.owner] is the agent
/// owner). Defensive against casing / surrounding whitespace from legacy rows.
bool isAgentTask(Task task) => task.owner.trim().toLowerCase() == kAgentOwner;

/// Returns the subset of [tasks] matching [filter], preserving input order.
///
///   * [TaskOwnerFilter.all]  → the list unchanged.
///   * [TaskOwnerFilter.mine] → everything that is NOT an agent task (so legacy
///     rows with a missing / unexpected owner still count as "mine", never
///     silently vanishing from the default human view).
///   * [TaskOwnerFilter.ai]   → only agent tasks.
List<Task> filterByOwner(List<Task> tasks, TaskOwnerFilter filter) {
  switch (filter) {
    case TaskOwnerFilter.all:
      return tasks;
    case TaskOwnerFilter.mine:
      return tasks.where((t) => !isAgentTask(t)).toList(growable: false);
    case TaskOwnerFilter.ai:
      return tasks.where(isAgentTask).toList(growable: false);
  }
}

/// The number of AI-created tasks in [tasks] — used to badge the "AI" chip so
/// the user knows whether the AI has queued anything before tapping it.
int countAgentTasks(List<Task> tasks) => tasks.where(isAgentTask).length;
