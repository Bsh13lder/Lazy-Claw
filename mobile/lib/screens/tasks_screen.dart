import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../models/task.dart';
import '../providers/tasks_provider.dart';
import 'storage_banners.dart';
import 'tasks/add_task_sheet.dart';
import 'tasks/task_detail_sheet.dart';
import 'tasks/task_row.dart';

// ── Sections ──────────────────────────────────────────────────────────────────

/// The four section buckets rendered by [TasksScreen].
enum _Section { overdue, today, upcoming, done }

extension _SectionLabel on _Section {
  String get label {
    switch (this) {
      case _Section.overdue:
        return 'Overdue';
      case _Section.today:
        return 'Today';
      case _Section.upcoming:
        return 'Upcoming';
      case _Section.done:
        return 'Done';
    }
  }

  IconData get emptyIcon {
    switch (this) {
      case _Section.overdue:
        return Icons.warning_amber_rounded;
      case _Section.today:
        return Icons.today_outlined;
      case _Section.upcoming:
        return Icons.event_outlined;
      case _Section.done:
        return Icons.check_circle_outline;
    }
  }
}

// ── Grouping helper ───────────────────────────────────────────────────────────

/// Splits [tasks] into the four section buckets.
Map<_Section, List<Task>> _groupTasks(List<Task> tasks) {
  final now = DateTime.now();
  final today = DateTime(now.year, now.month, now.day);

  final overdue = <Task>[];
  final todayList = <Task>[];
  final upcoming = <Task>[];
  final done = <Task>[];

  for (final task in tasks) {
    if (task.isDone) {
      done.add(task);
      continue;
    }
    if (task.dueDate == null) {
      upcoming.add(task);
      continue;
    }
    final DateTime dueDay;
    try {
      final d = DateTime.parse(task.dueDate!);
      dueDay = DateTime(d.year, d.month, d.day);
    } catch (_) {
      upcoming.add(task);
      continue;
    }
    if (dueDay.isBefore(today)) {
      overdue.add(task);
    } else if (dueDay == today) {
      todayList.add(task);
    } else {
      upcoming.add(task);
    }
  }

  return {
    _Section.overdue: overdue,
    _Section.today: todayList,
    _Section.upcoming: upcoming,
    _Section.done: done,
  };
}

// ── Screen ────────────────────────────────────────────────────────────────────

class TasksScreen extends ConsumerStatefulWidget {
  const TasksScreen({super.key});

  @override
  ConsumerState<TasksScreen> createState() => _TasksScreenState();
}

class _TasksScreenState extends ConsumerState<TasksScreen> {
  @override
  void initState() {
    super.initState();
    // Load tasks from the local cache on first render (offline-first).
    Future.microtask(() => ref.read(tasksProvider.notifier).load());
  }

  Future<void> _refresh() => ref.read(tasksProvider.notifier).refresh();

  Future<void> _openAddSheet() async {
    final result = await showAddTaskSheet(context);
    if (result == null || !mounted) return;
    await ref.read(tasksProvider.notifier).addTask(
          result.title,
          priority: result.priority,
          dueDate: result.dueDate,
        );
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(tasksProvider);
    final reachable = ref.watch(reachableProvider);
    final degraded = ref.watch(dbHealthProvider).isDegraded;

    // Show error snackbar on new errors.
    ref.listen<TasksState>(tasksProvider, (prev, next) {
      if (next.error != null && next.error != prev?.error) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            backgroundColor: AppColors.bgSurfaceElevated,
            content: Text(next.error!, style: AppText.body),
            action: SnackBarAction(
              label: 'Dismiss',
              textColor: AppColors.accent,
              onPressed: () =>
                  ref.read(tasksProvider.notifier).clearError(),
            ),
          ),
        );
      }
    });

    return LzScaffold(
      appBar: LzAppBar(
        title: 'Tasks',
        large: true,
        gradientTitle: true,
        actions: [
          LzIconButton(
            icon: Icons.add,
            tooltip: 'New task',
            onPressed: _openAddSheet,
          ),
        ],
      ),
      banner: buildStorageBanners(
        context,
        offline: !reachable,
        degraded: degraded,
        onRetry: () => ref.read(tasksProvider.notifier).load(),
      ),
      floatingActionButton: FloatingActionButton(
        backgroundColor: AppColors.accent,
        foregroundColor: AppColors.onAccent,
        tooltip: 'New task',
        onPressed: _openAddSheet,
        child: const Icon(Icons.add),
      ),
      body: _buildBody(state),
    );
  }

  Widget _buildBody(TasksState state) {
    // ── Loading skeleton ─────────────────────────────────────────────────────
    // Only on the first instant cache read (no items yet, nothing errored).
    if (state.isLoading && state.tasks.isEmpty && state.error == null) {
      return LzSkeleton.list(
        count: 6,
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.md,
        ),
      );
    }

    // ── Error state ──────────────────────────────────────────────────────────
    // Nothing cached to show + a load error → offer a real Retry instead of
    // a misleading "empty" or an infinite skeleton.
    if (state.tasks.isEmpty && state.error != null) {
      return LzErrorState(
        message: state.error!,
        onRetry: () => ref.read(tasksProvider.notifier).load(),
      );
    }

    // ── Overall empty state ──────────────────────────────────────────────────
    if (!state.isLoading && state.tasks.isEmpty && state.error == null) {
      return LzEmptyState(
        icon: Icons.task_alt_outlined,
        title: 'No tasks yet',
        hint: 'Tap + to add your first task.',
        actionLabel: 'Add task',
        actionIcon: Icons.add,
        onAction: _openAddSheet,
      );
    }

    // ── Sectioned list ───────────────────────────────────────────────────────
    final grouped = _groupTasks(state.tasks);

    return LzRefresh(
      onRefresh: _refresh,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.lg,
          AppSpacing.md,
          AppSpacing.lg,
          AppSpacing.xxxl, // leave room above the FAB
        ),
        children: [
          for (final section in _Section.values)
            _TaskSection(
              section: section,
              tasks: grouped[section] ?? const [],
              dirtyIds: state.dirtyIds,
              onComplete: (id) =>
                  ref.read(tasksProvider.notifier).completeTask(id),
              onDelete: (id) =>
                  ref.read(tasksProvider.notifier).deleteTask(id),
              onOpen: (task) => showTaskDetailSheet(context, ref, task),
            ),
        ],
      ),
    );
  }
}

// ── Section widget ─────────────────────────────────────────────────────────────

class _TaskSection extends StatefulWidget {
  const _TaskSection({
    required this.section,
    required this.tasks,
    required this.dirtyIds,
    required this.onComplete,
    required this.onDelete,
    required this.onOpen,
  });

  final _Section section;
  final List<Task> tasks;
  final Set<String> dirtyIds;
  final void Function(String id) onComplete;
  final void Function(String id) onDelete;
  final void Function(Task task) onOpen;

  @override
  State<_TaskSection> createState() => _TaskSectionState();
}

class _TaskSectionState extends State<_TaskSection> {
  // "Done" section starts collapsed.
  bool _collapsed = false;

  @override
  void initState() {
    super.initState();
    _collapsed = widget.section == _Section.done;
  }

  @override
  Widget build(BuildContext context) {
    // Never render the section header if there's nothing to show (and it's not
    // the upcoming section which always appears as the catch-all bucket).
    if (widget.tasks.isEmpty &&
        widget.section != _Section.upcoming &&
        widget.section != _Section.today) {
      return const SizedBox.shrink();
    }

    final count = widget.tasks.length;
    final countBadge = count > 0
        ? Text(
            count.toString(),
            style: AppText.caption.copyWith(color: AppColors.textMuted),
          )
        : null;

    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.xl),
      child: LzSection(
        title: widget.section.label,
        action: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (countBadge != null) ...[
              countBadge,
              const SizedBox(width: AppSpacing.sm),
            ],
            if (widget.section == _Section.done)
              GestureDetector(
                onTap: () => setState(() => _collapsed = !_collapsed),
                child: Icon(
                  _collapsed
                      ? Icons.keyboard_arrow_down
                      : Icons.keyboard_arrow_up,
                  size: 18,
                  color: AppColors.textMuted,
                ),
              ),
          ],
        ),
        child: _buildContent(),
      ),
    );
  }

  Widget _buildContent() {
    if (_collapsed) return const SizedBox.shrink();

    if (widget.tasks.isEmpty) {
      return LzEmptyState(
        icon: widget.section.emptyIcon,
        title: _emptyTitle(widget.section),
      );
    }

    return Column(
      children: [
        for (int i = 0; i < widget.tasks.length; i++) ...[
          TaskRow(
            task: widget.tasks[i],
            pendingSync:
                widget.dirtyIds.contains(widget.tasks[i].id),
            onComplete: () => widget.onComplete(widget.tasks[i].id),
            onDelete: () => widget.onDelete(widget.tasks[i].id),
            onTap: () => widget.onOpen(widget.tasks[i]),
          ),
          if (i < widget.tasks.length - 1)
            const SizedBox(height: AppSpacing.sm),
        ],
      ],
    );
  }

  String _emptyTitle(_Section section) {
    switch (section) {
      case _Section.today:
        return 'Nothing due today';
      case _Section.upcoming:
        return 'No upcoming tasks';
      default:
        return 'All clear';
    }
  }
}
