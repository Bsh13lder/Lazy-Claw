import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../models/project.dart';
import '../../models/subtask.dart';
import '../../models/task.dart';
import '../expenses/project_color_picker.dart';
import 'ai_task_badge.dart';
import 'connected_task_row.dart';
import 'task_project_grouping.dart';

/// The Tasks-tab "Projects" view: a collapsible list of project buckets, each
/// showing a color dot, the project name and an open/total task count. Tapping
/// a bucket expands its tasks inline (reusing [ConnectedTaskRow]). Tasks are
/// matched to projects by a case-insensitive `category` → name match, with a
/// first-class "Inbox" bucket (leading the list) holding every projectless task
/// (see [groupTasksByProject]).
///
/// Purely presentational — all mutating callbacks live in the parent
/// [TasksScreen] so commits route through `tasksProvider`.
class TasksProjectView extends StatefulWidget {
  const TasksProjectView({
    super.key,
    required this.tasks,
    required this.projects,
    required this.dirtyIds,
    required this.onComplete,
    required this.onDelete,
    required this.onOpen,
    this.onRenameTitle,
    this.onPriorityChanged,
    this.onDueDateChanged,
    this.onCategoryChanged,
    this.onSubtasksChanged,
  });

  final List<Task> tasks;
  final List<Project> projects;
  final Set<String> dirtyIds;

  final void Function(String id) onComplete;
  final void Function(String id) onDelete;
  final void Function(Task task) onOpen;
  final void Function(String id, String title)? onRenameTitle;
  final void Function(String id, String priority)? onPriorityChanged;
  final void Function(String id, String dueDate)? onDueDateChanged;
  final void Function(String id, String category)? onCategoryChanged;
  final void Function(String id, List<Subtask> subtasks)? onSubtasksChanged;

  @override
  State<TasksProjectView> createState() => _TasksProjectViewState();
}

class _TasksProjectViewState extends State<TasksProjectView> {
  /// Names of the currently-expanded project buckets. Collapsed by default.
  final Set<String> _expanded = <String>{};

  void _toggle(String name) {
    HapticFeedback.selectionClick();
    setState(() {
      if (!_expanded.remove(name)) _expanded.add(name);
    });
  }

  @override
  Widget build(BuildContext context) {
    final groups = groupTasksByProject(widget.tasks, widget.projects);
    final ordered = orderedProjectGroupNames(widget.projects, groups);

    // The Inbox bucket is always seeded, so [ordered] is never literally empty.
    // Treat "no real projects AND no tasks at all" (only an empty Inbox) as the
    // empty state — there's nothing meaningful to group yet.
    final hasProjects = widget.projects.any((p) => p.name.trim().isNotEmpty);
    if (!hasProjects && widget.tasks.isEmpty) {
      return LzEmptyState(
        icon: Icons.folder_open_outlined,
        title: 'No projects yet',
        hint: 'Add a project from the Money tab, or tag a task with one.',
      );
    }

    // A lowercased name → Project lookup so each bucket can show its accent dot.
    final byName = <String, Project>{};
    for (final p in widget.projects) {
      byName[p.name.toLowerCase()] = p;
    }

    return ListView(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.lg,
        AppSpacing.md,
        AppSpacing.lg,
        AppSpacing.xxxl, // leave room above the FAB
      ),
      children: [
        for (final name in ordered)
          Padding(
            padding: const EdgeInsets.only(bottom: AppSpacing.md),
            child: _ProjectBucket(
              name: name,
              project: byName[name.toLowerCase()],
              tasks: groups[name] ?? const [],
              expanded: _expanded.contains(name),
              onToggle: () => _toggle(name),
              dirtyIds: widget.dirtyIds,
              projects: widget.projects,
              onComplete: widget.onComplete,
              onDelete: widget.onDelete,
              onOpen: widget.onOpen,
              onRenameTitle: widget.onRenameTitle,
              onPriorityChanged: widget.onPriorityChanged,
              onDueDateChanged: widget.onDueDateChanged,
              onCategoryChanged: widget.onCategoryChanged,
              onSubtasksChanged: widget.onSubtasksChanged,
            ),
          ),
      ],
    );
  }
}

/// One project bucket: a tappable header (dot + name + count) that expands to
/// reveal the bucket's tasks.
class _ProjectBucket extends StatelessWidget {
  const _ProjectBucket({
    required this.name,
    required this.project,
    required this.tasks,
    required this.expanded,
    required this.onToggle,
    required this.dirtyIds,
    required this.projects,
    required this.onComplete,
    required this.onDelete,
    required this.onOpen,
    this.onRenameTitle,
    this.onPriorityChanged,
    this.onDueDateChanged,
    this.onCategoryChanged,
    this.onSubtasksChanged,
  });

  final String name;
  final Project? project;
  final List<Task> tasks;
  final bool expanded;
  final VoidCallback onToggle;
  final Set<String> dirtyIds;
  final List<Project> projects;

  final void Function(String id) onComplete;
  final void Function(String id) onDelete;
  final void Function(Task task) onOpen;
  final void Function(String id, String title)? onRenameTitle;
  final void Function(String id, String priority)? onPriorityChanged;
  final void Function(String id, String dueDate)? onDueDateChanged;
  final void Function(String id, String category)? onCategoryChanged;
  final void Function(String id, List<Subtask> subtasks)? onSubtasksChanged;

  /// The Inbox bucket reads as a neutral, project-less home rather than a
  /// colored project — so it shows an inbox glyph instead of a color dot.
  bool get _isInbox => name == kInboxProjectLabel;

  @override
  Widget build(BuildContext context) {
    final counts = projectGroupCounts(tasks);
    final allDone = counts.total > 0 && counts.open == 0;

    return LzCard(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // ── Header ─────────────────────────────────────────────────────────
          Material(
            color: Colors.transparent,
            borderRadius: AppRadii.rLg,
            child: InkWell(
              key: ValueKey('project-bucket-$name'),
              onTap: onToggle,
              borderRadius: AppRadii.rLg,
              child: Padding(
                padding: const EdgeInsets.symmetric(
                  horizontal: AppSpacing.md,
                  vertical: AppSpacing.md,
                ),
                child: Row(
                  children: [
                    if (_isInbox)
                      const Icon(
                        Icons.inbox_outlined,
                        size: 16,
                        color: AppColors.textMuted,
                      )
                    else
                      ProjectColorDot(hex: project?.color, size: 10),
                    const SizedBox(width: AppSpacing.md),
                    Expanded(
                      child: Text(
                        name,
                        style: AppText.body.copyWith(
                          fontWeight: FontWeight.w600,
                        ),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    const SizedBox(width: AppSpacing.sm),
                    _CountBadge(
                      open: counts.open,
                      total: counts.total,
                      allDone: allDone,
                    ),
                    const SizedBox(width: AppSpacing.xs),
                    AnimatedRotation(
                      turns: expanded ? 0.5 : 0,
                      duration: AppMotion.fast,
                      child: const Icon(
                        Icons.keyboard_arrow_down,
                        size: 20,
                        color: AppColors.textMuted,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),

          // ── Tasks (when expanded) ─────────────────────────────────────────
          if (expanded)
            Padding(
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.md,
                0,
                AppSpacing.md,
                AppSpacing.md,
              ),
              child: tasks.isEmpty
                  ? Align(
                      alignment: Alignment.centerLeft,
                      child: Text(
                        'No tasks in this project',
                        style: AppText.caption
                            .copyWith(color: AppColors.textMuted),
                      ),
                    )
                  : Column(
                      children: [
                        for (int i = 0; i < tasks.length; i++) ...[
                          AgentTaskBadged(
                            task: tasks[i],
                            child: ConnectedTaskRow(
                              task: tasks[i],
                              pendingSync: dirtyIds.contains(tasks[i].id),
                              projects: projects,
                              onComplete: onComplete,
                              onDelete: onDelete,
                              onOpen: onOpen,
                              onRenameTitle: onRenameTitle,
                              onPriorityChanged: onPriorityChanged,
                              onDueDateChanged: onDueDateChanged,
                              onCategoryChanged: onCategoryChanged,
                              onSubtasksChanged: onSubtasksChanged,
                            ),
                          ),
                          if (i < tasks.length - 1)
                            const SizedBox(height: AppSpacing.sm),
                        ],
                      ],
                    ),
            ),
        ],
      ),
    );
  }
}

/// A compact tonal `open/total` badge for a project bucket header. Reads emerald
/// with a leading check when the bucket is fully cleared (every task done).
class _CountBadge extends StatelessWidget {
  const _CountBadge({
    required this.open,
    required this.total,
    required this.allDone,
  });

  final int open;
  final int total;
  final bool allDone;

  @override
  Widget build(BuildContext context) {
    final fg = allDone ? AppColors.success : AppColors.textSecondary;
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.sm,
        vertical: 2,
      ),
      decoration: BoxDecoration(
        color: AppColors.bgSurfaceElevated,
        borderRadius: AppRadii.rPill,
        border: Border.all(color: AppColors.borderSubtle),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (allDone) ...[
            const Icon(Icons.check_rounded, size: 12, color: AppColors.success),
            const SizedBox(width: AppSpacing.xs),
          ],
          Text(
            '$open/$total',
            style: AppText.caption.copyWith(
              color: fg,
              fontWeight: FontWeight.w700,
            ),
          ),
        ],
      ),
    );
  }
}
