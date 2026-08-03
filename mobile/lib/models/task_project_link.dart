import 'project.dart';
import 'task.dart';

/// Tasks that belong to project [p] — the same category<->name_key join the
/// server and agent use to line up a task's free-text `category` with a
/// project. Matching is case-insensitive against [Project.nameKey] (falling
/// back to the lower-cased [Project.name] for legacy projects created before
/// `nameKey` existed, mirroring [Project.isInbox]). A null/blank `category`
/// never matches — those tasks aren't linked to any project. Backs the
/// expense detail sheet's + bulk-assign sheet's task pickers: only a task in
/// the expense's own project should be selectable as its linked task.
///
/// [includeCompleted] defaults to `false`: a task whose [Task.isDone] is true
/// (`status == 'done'`) is excluded from the pickers, same as every other
/// done-task filter in the app (see `Task.isDone` call sites). Pass `true` to
/// include done tasks too (e.g. a future "show completed" toggle).
List<Task> tasksForProject(
  List<Task> all,
  Project p, {
  bool includeCompleted = false,
}) {
  final key = p.nameKey ?? p.name.toLowerCase();
  return [
    for (final t in all)
      if (_matchesProjectKey(t, key) && (includeCompleted || !t.isDone)) t,
  ];
}

bool _matchesProjectKey(Task t, String key) {
  final category = (t.category ?? '').trim().toLowerCase();
  return category.isNotEmpty && category == key;
}
