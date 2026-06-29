import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../models/project.dart';
import '../../models/subtask.dart';
import '../../models/task.dart';
import '../tasks_screen.dart' show isOverdueTask;
import 'ai_task_badge.dart';
import 'connected_task_row.dart';
import 'reschedule_sheet.dart';

/// The dedicated **Overdue** view — a first-class peer of List · Calendar ·
/// Projects. It shows only overdue tasks (already narrowed by the active owner
/// filter upstream), oldest-due first (most overdue on top), and a header that
/// reschedules the whole set in one tap.
///
/// Every row is a [ConnectedTaskRow] so all the per-task controls still work:
/// tap-the-due-chip → reschedule sheet, complete, open detail, swipe. The
/// callbacks are threaded straight from the screen, exactly like the List view.
class OverdueView extends ConsumerWidget {
  const OverdueView({
    super.key,
    required this.tasks,
    required this.projects,
    required this.dirtyIds,
    required this.onRefresh,
    required this.onComplete,
    required this.onDelete,
    required this.onOpen,
    required this.onReschedule,
    required this.onRenameTitle,
    required this.onPriorityChanged,
    required this.onDueDateChanged,
    required this.onCategoryChanged,
    required this.onSubtasksChanged,
  });

  /// The owner-filtered task list (List/Calendar/Projects all render this same
  /// set); this view picks the overdue subset out of it.
  final List<Task> tasks;
  final List<Project> projects;
  final Set<String> dirtyIds;

  final Future<void> Function() onRefresh;
  final void Function(String id) onComplete;
  final void Function(String id) onDelete;
  final void Function(Task task) onOpen;
  final void Function(Task task) onReschedule;
  final void Function(String id, String title) onRenameTitle;
  final void Function(String id, String priority) onPriorityChanged;
  final void Function(String id, String dueDate) onDueDateChanged;
  final void Function(String id, String category) onCategoryChanged;
  final void Function(String id, List<Subtask> subtasks) onSubtasksChanged;

  /// The overdue subset, oldest-due first (most overdue on top). Reuses the
  /// shared [isOverdueTask] rule so it matches the inline Overdue section.
  List<Task> get _overdue {
    final out = tasks.where(isOverdueTask).toList()
      ..sort((a, b) => (a.dueDate ?? '').compareTo(b.dueDate ?? ''));
    return out;
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final overdue = _overdue;

    if (overdue.isEmpty) {
      // Wrapped in a scroll view so pull-to-refresh still works on the empty
      // state (a bare Center isn't scrollable).
      return LzRefresh(
        onRefresh: onRefresh,
        child: ListView(
          children: const [
            SizedBox(height: AppSpacing.xxxl),
            LzEmptyState(
              icon: Icons.check_circle_outline,
              title: 'All clear',
              hint: 'No overdue tasks.',
            ),
          ],
        ),
      );
    }

    return LzRefresh(
      onRefresh: onRefresh,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.lg,
          AppSpacing.md,
          AppSpacing.lg,
          AppSpacing.xxxl, // leave room above the FAB
        ),
        children: [
          _OverdueHeader(
            count: overdue.length,
            onRescheduleAll: () =>
                showRescheduleAllSheet(context, ref, overdue),
          ),
          const SizedBox(height: AppSpacing.md),
          for (int i = 0; i < overdue.length; i++) ...[
            AgentTaskBadged(
              task: overdue[i],
              child: ConnectedTaskRow(
                task: overdue[i],
                pendingSync: dirtyIds.contains(overdue[i].id),
                projects: projects,
                onComplete: onComplete,
                onDelete: onDelete,
                onOpen: onOpen,
                onReschedule: onReschedule,
                onRenameTitle: onRenameTitle,
                onPriorityChanged: onPriorityChanged,
                onDueDateChanged: onDueDateChanged,
                onCategoryChanged: onCategoryChanged,
                onSubtasksChanged: onSubtasksChanged,
              ),
            ),
            if (i < overdue.length - 1) const SizedBox(height: AppSpacing.sm),
          ],
        ],
      ),
    );
  }
}

/// The overdue header: a red-toned "N overdue" count + a "Reschedule all"
/// button that opens the generalized reschedule sheet for the whole set.
class _OverdueHeader extends StatelessWidget {
  const _OverdueHeader({required this.count, required this.onRescheduleAll});

  final int count;
  final VoidCallback onRescheduleAll;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Icon(
          Icons.warning_amber_rounded,
          size: 18,
          color: AppColors.error,
        ),
        const SizedBox(width: AppSpacing.sm),
        Expanded(
          child: Text(
            '$count overdue',
            style: AppText.title.copyWith(color: AppColors.textPrimary),
          ),
        ),
        LzButton.secondary(
          label: 'Reschedule all',
          icon: Icons.event_repeat_outlined,
          onPressed: onRescheduleAll,
        ),
      ],
    );
  }
}
