import '../../models/project.dart';
import '../../models/task.dart';

/// Pure, framework-free helpers backing the Tasks "Projects" view.
///
/// They bucket tasks under their project (matching `task.category` to a
/// project name, case-insensitive) so the view can render a project → tasks
/// breakdown. Kept widget-free so the matching + counting logic is trivially
/// unit-testable.

/// The label for the catch-all "Inbox" bucket holding tasks with no project (a
/// null / blank `category`). This is the first-class home for projectless
/// tasks — the Tasks analogue of the "General" fallback used for projectless
/// expenses.
const String kInboxProjectLabel = 'Inbox';

/// Deprecated alias kept so existing callers/tests referencing the old
/// "Uncategorized" name keep compiling. Prefer [kInboxProjectLabel].
@Deprecated('Renamed to kInboxProjectLabel ("Inbox")')
const String kUncategorizedProjectLabel = kInboxProjectLabel;

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
///   * A task with a null / blank `category` lands under the first-class
///     [kInboxProjectLabel] bucket.
///
/// The Inbox bucket is **always seeded** (even with zero projectless tasks) so
/// it's a permanent, clearly-labelled home for loose tasks.
Map<String, List<Task>> groupTasksByProject(
  List<Task> tasks,
  List<Project> projects,
) {
  final canonical = <String, String>{};
  final out = <String, List<Task>>{};

  // Inbox is a first-class, always-present bucket — seed it up front.
  out.putIfAbsent(kInboxProjectLabel, () => <Task>[]);

  for (final p in projects) {
    final name = p.name.trim();
    if (name.isEmpty) continue;
    canonical[name.toLowerCase()] = p.name;
    out.putIfAbsent(p.name, () => <Task>[]);
  }

  for (final task in tasks) {
    final raw = task.category?.trim() ?? '';
    if (raw.isEmpty) {
      (out[kInboxProjectLabel] ??= <Task>[]).add(task);
      continue;
    }
    final key = canonical[raw.toLowerCase()] ?? raw;
    (out[key] ??= <Task>[]).add(task);
  }

  return out;
}

/// The display order for the group keys in [groups]: the first-class
/// [kInboxProjectLabel] bucket FIRST (it's the home for loose tasks, so it
/// leads), then the projects (in their [projects] order), then any extra
/// category-only groups (sorted).
List<String> orderedProjectGroupNames(
  List<Project> projects,
  Map<String, List<Task>> groups,
) {
  final out = <String>[];
  final seen = <String>{};

  // Inbox always leads when present (it's seeded by [groupTasksByProject]).
  if (groups.containsKey(kInboxProjectLabel)) {
    out.add(kInboxProjectLabel);
    seen.add(kInboxProjectLabel);
  }

  for (final p in projects) {
    final name = p.name;
    if (name.trim().isEmpty) continue;
    if (groups.containsKey(name) && seen.add(name)) out.add(name);
  }

  final extras = groups.keys
      .where((k) => k != kInboxProjectLabel && !seen.contains(k))
      .toList()
    ..sort();
  out.addAll(extras);

  return out;
}

/// The `(open, total)` counts for a group's [tasks] — open = not done.
({int open, int total}) projectGroupCounts(List<Task> tasks) =>
    (open: tasks.where((t) => !t.isDone).length, total: tasks.length);

// ──────────────────────────────────────────────────────────────────────────
// Split grouping: real Projects vs tag-only categories vs Inbox
// ──────────────────────────────────────────────────────────────────────────

/// One real-project bucket: the [project] (carried for its accent color) and
/// the [tasks] whose `category` matched its name (case-insensitively). Seeded
/// even with zero tasks so empty projects still appear in the view.
class ProjectBucketData {
  const ProjectBucketData({required this.project, required this.tasks});

  final Project project;
  final List<Task> tasks;

  /// The canonical (project-cased) display name.
  String get name => project.name;
}

/// One tag-only bucket: a [name] (the category string the user typed) that does
/// NOT correspond to any real [Project], plus its [tasks]. There's no project to
/// carry, so the view renders these with a neutral tag glyph instead of a color
/// dot. Only built for categories that actually have tasks.
class TagBucketData {
  const TagBucketData({required this.name, required this.tasks});

  final String name;
  final List<Task> tasks;
}

/// The fully-separated grouping backing the Tasks "Projects" view:
///   * [realProjects] — one bucket per real [Project] (seeded even when empty),
///     in the project list's order, keyed by canonical name.
///   * [tags] — category strings that match no real project, alphabetically
///     ordered. Only categories with at least one task appear here.
///   * [inbox] — tasks with a null / blank category (the projectless home).
///
/// Pure + immutable so the view + tests stay trivial.
class SplitTaskGroups {
  const SplitTaskGroups({
    required this.realProjects,
    required this.tags,
    required this.inbox,
  });

  final List<ProjectBucketData> realProjects;
  final List<TagBucketData> tags;
  final List<Task> inbox;

  bool get hasRealProjects => realProjects.isNotEmpty;
  bool get hasTags => tags.isNotEmpty;
}

/// Splits [tasks] into real **Projects**, tag-only **categories**, and the
/// **Inbox** — the separation the Projects view renders as three sections.
///
/// Matching mirrors [groupTasksByProject]: a task's trimmed `category` is
/// matched **case-insensitively** to a project name. Rules:
///   * A task whose category matches a real project lands under that project
///     (keyed by the project's canonical name).
///   * Every project in [projects] seeds a [ProjectBucketData] (zero-task
///     projects still appear), preserving the [projects] order.
///   * A task whose category is set but matches NO real project becomes a
///     [TagBucketData] (tags are sorted; only non-empty tags are emitted).
///   * A task with a null / blank category falls into [SplitTaskGroups.inbox].
SplitTaskGroups splitTasksByGroup(
  List<Task> tasks,
  List<Project> projects,
) {
  // Canonical lookup: lowercased project name → Project (first wins on dupes).
  final canonical = <String, Project>{};
  for (final p in projects) {
    final name = p.name.trim();
    if (name.isEmpty) continue;
    canonical.putIfAbsent(name.toLowerCase(), () => p);
  }

  // Seed a bucket list per project (preserving order), plus by-name accumulators
  // so we can append matched tasks without re-scanning.
  final projectTasks = <String, List<Task>>{};
  final orderedProjects = <Project>[];
  final seenProject = <String>{};
  for (final p in projects) {
    final name = p.name.trim();
    if (name.isEmpty) continue;
    final lower = name.toLowerCase();
    if (!seenProject.add(lower)) continue; // skip duplicate project names
    orderedProjects.add(p);
    projectTasks[lower] = <Task>[];
  }

  final tagTasks = <String, List<Task>>{}; // canonical-cased tag → tasks
  final inbox = <Task>[];

  for (final task in tasks) {
    final raw = task.category?.trim() ?? '';
    if (raw.isEmpty) {
      inbox.add(task);
      continue;
    }
    final lower = raw.toLowerCase();
    final project = canonical[lower];
    if (project != null) {
      projectTasks[lower]!.add(task);
    } else {
      // Preserve the first-seen casing for the tag label.
      (tagTasks[raw] ??= <Task>[]).add(task);
    }
  }

  final realProjects = [
    for (final p in orderedProjects)
      ProjectBucketData(
        project: p,
        tasks: projectTasks[p.name.trim().toLowerCase()] ?? const <Task>[],
      ),
  ];

  final tagNames = tagTasks.keys.toList()
    ..sort((a, b) => a.toLowerCase().compareTo(b.toLowerCase()));
  final tags = [
    for (final name in tagNames)
      TagBucketData(name: name, tasks: tagTasks[name]!),
  ];

  return SplitTaskGroups(
    realProjects: realProjects,
    tags: tags,
    inbox: inbox,
  );
}
