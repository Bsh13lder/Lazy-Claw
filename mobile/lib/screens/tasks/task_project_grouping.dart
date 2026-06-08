import '../../models/project.dart';
import '../../models/task.dart';

/// Pure, framework-free helpers backing the Tasks "Projects" view.
///
/// They bucket tasks under their project (matching `task.category` to a
/// project name, case-insensitive) so the view can render a project → tasks
/// breakdown. Kept widget-free so the matching + counting logic is trivially
/// unit-testable.

/// The label for the catch-all bucket holding tasks with no project (a null /
/// blank `category`).
const String kUncategorizedProjectLabel = 'Uncategorized';

/// Buckets [tasks] by their project.
///
/// Matching is **case-insensitive** against each project's name, and the
/// canonical (project-cased) name is used as the map key so `home` and `Home`
/// collapse into the project's real name. Rules:
///   * Every project in [projects] seeds an entry (so a project with zero tasks
///     still appears in the view).
///   * A task whose `category` matches a project lands under that project.
///   * A task whose `category` is set but matches no project lands under its
///     own category string (it's still a real grouping the user assigned).
///   * A task with a null / blank `category` lands under
///     [kUncategorizedProjectLabel].
Map<String, List<Task>> groupTasksByProject(
  List<Task> tasks,
  List<Project> projects,
) {
  final canonical = <String, String>{};
  final out = <String, List<Task>>{};

  for (final p in projects) {
    final name = p.name.trim();
    if (name.isEmpty) continue;
    canonical[name.toLowerCase()] = p.name;
    out.putIfAbsent(p.name, () => <Task>[]);
  }

  for (final task in tasks) {
    final raw = task.category?.trim() ?? '';
    if (raw.isEmpty) {
      (out[kUncategorizedProjectLabel] ??= <Task>[]).add(task);
      continue;
    }
    final key = canonical[raw.toLowerCase()] ?? raw;
    (out[key] ??= <Task>[]).add(task);
  }

  return out;
}

/// The display order for the group keys in [groups]: projects first (in their
/// [projects] order), then any extra category-only groups (sorted), then the
/// [kUncategorizedProjectLabel] bucket last.
List<String> orderedProjectGroupNames(
  List<Project> projects,
  Map<String, List<Task>> groups,
) {
  final out = <String>[];
  final seen = <String>{};

  for (final p in projects) {
    final name = p.name;
    if (name.trim().isEmpty) continue;
    if (groups.containsKey(name) && seen.add(name)) out.add(name);
  }

  final extras = groups.keys
      .where((k) => k != kUncategorizedProjectLabel && !seen.contains(k))
      .toList()
    ..sort();
  out.addAll(extras);

  if (groups.containsKey(kUncategorizedProjectLabel)) {
    out.add(kUncategorizedProjectLabel);
  }

  return out;
}

/// The `(open, total)` counts for a group's [tasks] — open = not done.
({int open, int total}) projectGroupCounts(List<Task> tasks) =>
    (open: tasks.where((t) => !t.isDone).length, total: tasks.length);
