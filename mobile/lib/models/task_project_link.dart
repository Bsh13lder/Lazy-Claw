import 'project.dart';
import 'task.dart';

/// Tasks that belong to project [p] — the same category<->name_key join the
/// server and agent use to line up a task's free-text `category` with a
/// project. Matching is case-insensitive against [Project.nameKey] (falling
/// back to the lower-cased [Project.name] for legacy projects created before
/// `nameKey` existed, mirroring [Project.isInbox]). A null/blank `category`
/// never matches — those tasks aren't linked to any project. Backs the
/// expense detail sheet's task picker: only a task in the expense's own
/// project should be selectable as its linked task.
List<Task> tasksForProject(List<Task> all, Project p) {
  final key = p.nameKey ?? p.name.toLowerCase();
  return [
    for (final t in all)
      if (_matchesProjectKey(t, key)) t,
  ];
}

bool _matchesProjectKey(Task t, String key) {
  final category = (t.category ?? '').trim().toLowerCase();
  return category.isNotEmpty && category == key;
}
