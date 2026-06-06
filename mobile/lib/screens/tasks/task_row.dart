import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../models/task.dart';

/// A single task row: priority chip, title, due chip, sync badge, and
/// a checkbox affordance. Wrapped in a Dismissible so the parent list
/// gets swipe-to-complete (startToEnd) and swipe-to-delete (endToStart).
///
/// Tapping the card body (anywhere outside the checkbox) fires [onTap] — the
/// Tasks screen wires this to open the detail/edit sheet. The checkbox keeps
/// its own tap (complete) and the swipe gestures are unchanged.
class TaskRow extends StatelessWidget {
  const TaskRow({
    super.key,
    required this.task,
    required this.pendingSync,
    required this.onComplete,
    required this.onDelete,
    this.onTap,
  });

  final Task task;
  final bool pendingSync;
  final VoidCallback onComplete;
  final VoidCallback onDelete;

  /// Opens the task detail/edit sheet. Null leaves the row non-tappable.
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final isDone = task.isDone;
    final priorityColor = _priorityColor(task.priority);

    return Dismissible(
      key: ValueKey('task-row-${task.id}'),
      // Done tasks only allow delete (endToStart). Active tasks allow both.
      direction: isDone
          ? DismissDirection.endToStart
          : DismissDirection.horizontal,
      background: _swipeBg(
        alignment: Alignment.centerLeft,
        color: AppColors.success.withValues(alpha: 0.18),
        icon: Icons.check_circle_outline,
        iconColor: AppColors.success,
      ),
      secondaryBackground: _swipeBg(
        alignment: Alignment.centerRight,
        color: AppColors.error.withValues(alpha: 0.18),
        icon: Icons.delete_outline,
        iconColor: AppColors.error,
      ),
      confirmDismiss: (direction) async {
        if (direction == DismissDirection.startToEnd) {
          // Complete in place — don't actually dismiss the tile.
          if (!isDone) onComplete();
          return false;
        }
        // endToStart → confirm delete.
        return LzConfirm.show(
          context,
          title: 'Delete task?',
          message: task.title,
          confirmLabel: 'Delete',
          danger: true,
        );
      },
      onDismissed: (_) => onDelete(),
      child: LzCard(
        onTap: onTap,
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.md,
          vertical: AppSpacing.md,
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            // ── Checkbox affordance ──────────────────────────────────────
            GestureDetector(
              onTap: isDone ? null : onComplete,
              child: AnimatedContainer(
                duration: AppMotion.fast,
                width: 24,
                height: 24,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: isDone
                      ? AppColors.success.withValues(alpha: 0.18)
                      : Colors.transparent,
                  border: Border.all(
                    color:
                        isDone ? AppColors.success : AppColors.borderDefault,
                    width: 1.5,
                  ),
                ),
                child: isDone
                    ? const Icon(Icons.check,
                        size: 15, color: AppColors.success)
                    : null,
              ),
            ),

            const SizedBox(width: AppSpacing.md),

            // ── Title + chips ────────────────────────────────────────────
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    task.title,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: isDone
                        ? AppText.body.copyWith(
                            color: AppColors.textMuted,
                            decoration: TextDecoration.lineThrough,
                            decorationColor: AppColors.textMuted,
                          )
                        : AppText.body,
                  ),
                  const SizedBox(height: AppSpacing.sm),
                  Wrap(
                    spacing: AppSpacing.xs,
                    runSpacing: AppSpacing.xs,
                    children: [
                      // Priority chip — always visible.
                      LzChip(
                        label: task.priority,
                        dense: true,
                        color: priorityColor,
                        selected: true,
                      ),
                      // Due date chip.
                      if (task.dueDate != null)
                        LzChip(
                          label: task.dueDate!,
                          dense: true,
                          icon: Icons.calendar_today_outlined,
                          color: _dueDateColor(task.dueDate!),
                          selected: !isDone,
                        ),
                      // Category chip (hidden for done tasks to keep it clean).
                      if (task.category != null && !isDone)
                        LzChip(
                          label: task.category!,
                          dense: true,
                          color: AppColors.info,
                        ),
                    ],
                  ),
                ],
              ),
            ),

            // ── Trailing: pending-sync badge ─────────────────────────────
            if (pendingSync) ...[
              const SizedBox(width: AppSpacing.sm),
              const LzSyncBadge(
                state: LzSyncState.offline,
                compact: true,
              ),
            ],
          ],
        ),
      ),
    );
  }

  Color _priorityColor(String priority) {
    switch (priority) {
      case 'urgent':
        return AppColors.error;
      case 'high':
        return AppColors.warn;
      case 'medium':
        return AppColors.info;
      default:
        return AppColors.textMuted;
    }
  }

  Color _dueDateColor(String dueDate) {
    try {
      final due = DateTime.parse(dueDate);
      final now = DateTime.now();
      final today = DateTime(now.year, now.month, now.day);
      final dueDay = DateTime(due.year, due.month, due.day);
      if (dueDay.isBefore(today)) return AppColors.error;
      if (dueDay == today) return AppColors.warn;
    } catch (_) {
      // Non-ISO string — show neutral colour.
    }
    return AppColors.textSecondary;
  }

  static Widget _swipeBg({
    required AlignmentGeometry alignment,
    required Color color,
    required IconData icon,
    required Color iconColor,
  }) {
    return Container(
      alignment: alignment,
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
      decoration: BoxDecoration(
        color: color,
        borderRadius: AppRadii.rLg,
      ),
      child: Icon(icon, color: iconColor, size: 24),
    );
  }
}
