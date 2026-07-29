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

/// The Tasks-tab "Projects" view. Buckets are split into three clearly-separated
/// sections (see [splitTasksByGroup]):
///   * **Projects** — real projects from the budgets/projects store, each with
///     its color dot + open/total count, plus the neutral **Inbox** bucket
///     leading the section as the projectless home.
///   * **Tags** — `category` strings the user assigned that match no real
///     project, rendered with a muted tag glyph instead of a color dot.
///
/// Tapping a bucket expands its tasks inline (reusing [ConnectedTaskRow]). Tasks
/// match projects by a case-insensitive `category` → name match.
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
    this.onAddProject,
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

  /// Create a new project directly from this view (app-bar + empty-state +
  /// section-header "+" all route here). Null hides those affordances.
  final VoidCallback? onAddProject;

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
    final split = splitTasksByGroup(widget.tasks, widget.projects);

    // Treat "no real projects AND no tasks at all" as the empty state — there's
    // nothing meaningful to group yet (the Inbox would be the only, empty bucket).
    final hasProjects = widget.projects.any((p) => p.name.trim().isNotEmpty);
    if (!hasProjects && widget.tasks.isEmpty) {
      return LzEmptyState(
        icon: Icons.folder_open_outlined,
        title: 'No projects yet',
        hint: 'Create a project to group your tasks, or tag a task with one.',
        actionLabel: widget.onAddProject != null ? 'Add project' : null,
        actionIcon: Icons.create_new_folder_outlined,
        onAction: widget.onAddProject,
      );
    }

    return ListView(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.lg,
        AppSpacing.md,
        AppSpacing.lg,
        AppSpacing.xxxl, // leave room above the FAB
      ),
      children: [
        // ── Projects (real projects + the Inbox home) ─────────────────────────
        if (split.hasRealProjects || widget.tasks.isNotEmpty)
          LzSection(
            title: 'Projects',
            action: widget.onAddProject == null
                ? null
                : GestureDetector(
                    behavior: HitTestBehavior.opaque,
                    onTap: widget.onAddProject,
                    child: Icon(
                      Icons.add_circle_outline,
                      size: 20,
                      color: AppColors.accent,
                    ),
                  ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                // Inbox leads as the neutral, projectless home.
                _bucketPadding(
                  _ProjectBucket(
                    name: kInboxProjectLabel,
                    project: null,
                    isTag: false,
                    tasks: split.inbox,
                    expanded: _expanded.contains(kInboxProjectLabel),
                    onToggle: () => _toggle(kInboxProjectLabel),
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
                for (final bucket in split.realProjects)
                  _bucketPadding(
                    _ProjectBucket(
                      name: bucket.name,
                      project: bucket.project,
                      isTag: false,
                      tasks: bucket.tasks,
                      expanded: _expanded.contains(bucket.name),
                      onToggle: () => _toggle(bucket.name),
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
            ),
          ),

        // ── Tags (category strings that are not real projects) ────────────────
        if (split.hasTags) ...[
          const SizedBox(height: AppSpacing.lg),
          LzSection(
            title: 'Tags',
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                for (final bucket in split.tags)
                  _bucketPadding(
                    _ProjectBucket(
                      name: bucket.name,
                      project: null,
                      isTag: true,
                      tasks: bucket.tasks,
                      expanded: _expanded.contains(bucket.name),
                      onToggle: () => _toggle(bucket.name),
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
            ),
          ),
        ],
      ],
    );
  }

  /// Bottom-spaces a bucket so consecutive cards don't touch.
  Widget _bucketPadding(Widget child) => Padding(
        padding: const EdgeInsets.only(bottom: AppSpacing.md),
        child: child,
      );
}

/// One bucket: a tappable header that expands to reveal the bucket's tasks. The
/// leading glyph adapts to the bucket kind — a project color dot for a real
/// project, an inbox glyph for the projectless [kInboxProjectLabel] home, and a
/// muted tag label glyph when [isTag] (a category that's not a real project).
class _ProjectBucket extends StatelessWidget {
  const _ProjectBucket({
    required this.name,
    required this.project,
    required this.isTag,
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

  /// Whether this is a tag-only bucket (a category with no real project) — it
  /// reads as a neutral tag rather than a colored project.
  final bool isTag;
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
  bool get _isInbox => !isTag && project == null && name == kInboxProjectLabel;

  /// The leading glyph for the header: a tag label for tag buckets, an inbox
  /// glyph for the Inbox home, or the project's accent color dot.
  Widget _leading() {
    if (isTag) {
      return const Icon(
        Icons.label_outline,
        size: 16,
        color: AppColors.textMuted,
      );
    }
    if (_isInbox) {
      return const Icon(
        Icons.inbox_outlined,
        size: 16,
        color: AppColors.textMuted,
      );
    }
    return ProjectColorDot(hex: project?.color, size: 10);
  }

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
                    _leading(),
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
                        _isInbox
                            ? 'No loose tasks'
                            : 'No tasks in this project',
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
