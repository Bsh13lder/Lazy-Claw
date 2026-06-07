import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:lazyclaw_mobile/core/due_date.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../models/subtask.dart';
import '../../models/task.dart';

/// A full task card: a checkbox affordance, an (optionally inline-editable)
/// title, a row of chips (project · priority · due date · time · subtask
/// progress), and a sync badge. Wrapped in a [Dismissible] so the parent list
/// gets swipe-to-complete (startToEnd) and swipe-to-delete (endToStart).
///
/// ## Tap model (Todoist/Taskade-style)
/// * **Tap the title text** → inline-edit the title (when [onTitleChanged] is
///   set); commits on submit or blur.
/// * **Single tap elsewhere on the card** → [onTap] (open the full detail /
///   settings sheet), instantly. A **double tap** opens it too — the first tap
///   already opens the sheet, so the user's "double-click → full settings"
///   gesture works without burdening single tap with a 300 ms double-tap
///   disambiguation delay. One destination, never surprising.
/// * **Checkbox tap** / **swipe** → complete; **swipe** (endToStart) → delete.
class TaskRow extends StatefulWidget {
  const TaskRow({
    super.key,
    required this.task,
    required this.pendingSync,
    required this.onComplete,
    required this.onDelete,
    this.onTap,
    this.onTitleChanged,
  });

  final Task task;
  final bool pendingSync;
  final VoidCallback onComplete;
  final VoidCallback onDelete;

  /// Opens the full task detail sheet (single OR double tap). Null leaves the
  /// card non-tappable.
  final VoidCallback? onTap;

  /// Commits an inline-edited title. Null disables inline title editing (the
  /// title renders as plain, non-tappable text).
  final ValueChanged<String>? onTitleChanged;

  @override
  State<TaskRow> createState() => _TaskRowState();
}

class _TaskRowState extends State<TaskRow> {
  late final TextEditingController _titleCtrl;
  late final FocusNode _titleFocus;
  bool _editingTitle = false;

  @override
  void initState() {
    super.initState();
    _titleCtrl = TextEditingController(text: widget.task.title);
    _titleFocus = FocusNode();
    // Commit when the inline field loses focus (tap away / open detail).
    _titleFocus.addListener(() {
      if (!_titleFocus.hasFocus && _editingTitle) _commitTitle();
    });
  }

  @override
  void dispose() {
    _titleCtrl.dispose();
    _titleFocus.dispose();
    super.dispose();
  }

  /// Fire a light haptic tick alongside completion so the gesture feels
  /// tactile (checkbox tap + swipe-to-complete both route through here).
  void _completeWithHaptic() {
    HapticFeedback.lightImpact();
    widget.onComplete();
  }

  void _beginEditTitle() {
    if (widget.onTitleChanged == null || widget.task.isDone) return;
    HapticFeedback.selectionClick();
    _titleCtrl.text = widget.task.title;
    _titleCtrl.selection = TextSelection(
      baseOffset: 0,
      extentOffset: _titleCtrl.text.length,
    );
    setState(() => _editingTitle = true);
    _titleFocus.requestFocus();
  }

  void _commitTitle() {
    final next = _titleCtrl.text.trim();
    setState(() => _editingTitle = false);
    if (next.isEmpty || next == widget.task.title) return;
    widget.onTitleChanged?.call(next);
  }

  @override
  Widget build(BuildContext context) {
    final task = widget.task;
    final isDone = task.isDone;
    final priorityColor = _priorityColor(task.priority);
    final progress = subtaskProgressLabel(task.subtasks);

    final card = LzCard(
      // Single tap opens the detail sheet (with ink ripple).
      onTap: _editingTitle ? null : widget.onTap,
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.md,
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          // ── Checkbox affordance ──────────────────────────────────────────
          GestureDetector(
            onTap: isDone ? null : _completeWithHaptic,
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
                  color: isDone ? AppColors.success : AppColors.borderDefault,
                  width: 1.5,
                ),
              ),
              child: isDone
                  ? const Icon(Icons.check, size: 15, color: AppColors.success)
                  : null,
            ),
          ),

          const SizedBox(width: AppSpacing.md),

          // ── Title + chips ────────────────────────────────────────────────
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _buildTitle(isDone),
                const SizedBox(height: AppSpacing.sm),
                Wrap(
                  spacing: AppSpacing.xs,
                  runSpacing: AppSpacing.xs,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    // Project / category chip (colored) — leads the row.
                    if (task.category != null && task.category!.isNotEmpty)
                      LzChip(
                        key: ValueKey('task-row-project-${task.id}'),
                        label: task.category!,
                        dense: true,
                        icon: Icons.folder_outlined,
                        color: AppColors.info,
                        selected: !isDone,
                      ),
                    // Priority chip — always visible.
                    LzChip(
                      label: task.priority,
                      dense: true,
                      color: priorityColor,
                      selected: !isDone,
                    ),
                    // Due date chip (date only — the time gets its own tag).
                    if (task.dueDate != null)
                      LzChip(
                        label: dueDateDayPart(task.dueDate!),
                        dense: true,
                        icon: Icons.calendar_today_outlined,
                        color: _dueDateColor(task.dueDate!),
                        selected: !isDone,
                      ),
                    // Time-of-day tag — only when the due date carries a time.
                    if (dueDateHasTime(task.dueDate))
                      LzChip(
                        key: ValueKey('task-row-time-${task.id}'),
                        label: formatDueTimeLabel(task.dueDate)!,
                        dense: true,
                        icon: Icons.schedule_outlined,
                        color: _dueDateColor(task.dueDate!),
                        selected: !isDone,
                      ),
                    // Subtask progress (done/total) — only when there are subs.
                    if (progress != null)
                      LzChip(
                        key: ValueKey('task-row-subtasks-${task.id}'),
                        label: progress,
                        dense: true,
                        icon: Icons.checklist_rounded,
                        color: AppColors.accent,
                        selected: !isDone,
                      ),
                  ],
                ),
              ],
            ),
          ),

          // ── Trailing: pending-sync badge ───────────────────────────────────
          if (widget.pendingSync) ...[
            const SizedBox(width: AppSpacing.sm),
            const LzSyncBadge(state: LzSyncState.offline, compact: true),
          ],
        ],
      ),
    );

    return Dismissible(
      key: ValueKey('task-row-${task.id}'),
      // Done tasks only allow delete (endToStart). Active tasks allow both.
      direction:
          isDone ? DismissDirection.endToStart : DismissDirection.horizontal,
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
          if (!isDone) _completeWithHaptic();
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
      onDismissed: (_) => widget.onDelete(),
      // Done tasks read visually distinct: the whole card is dimmed (on top of
      // the strikethrough title).
      child: isDone ? Opacity(opacity: 0.62, child: card) : card,
    );
  }

  /// The title: an inline [TextField] while editing, otherwise a (tappable when
  /// [TaskRow.onTitleChanged] is set) [Text]. Done tasks render struck-through.
  Widget _buildTitle(bool isDone) {
    if (_editingTitle) {
      return TextField(
        key: ValueKey('task-row-title-edit-${widget.task.id}'),
        controller: _titleCtrl,
        focusNode: _titleFocus,
        style: AppText.body,
        cursorColor: AppColors.accent,
        maxLines: 2,
        minLines: 1,
        textInputAction: TextInputAction.done,
        onSubmitted: (_) => _commitTitle(),
        decoration: const InputDecoration(
          isDense: true,
          contentPadding: EdgeInsets.symmetric(vertical: 4),
          border: UnderlineInputBorder(),
        ),
      );
    }

    final text = Text(
      widget.task.title,
      maxLines: 2,
      overflow: TextOverflow.ellipsis,
      style: isDone
          ? AppText.body.copyWith(
              color: AppColors.textMuted,
              decoration: TextDecoration.lineThrough,
              decorationColor: AppColors.textMuted,
            )
          : AppText.body,
    );

    // Inline-edit hotspot: tapping the title text enters edit mode (when
    // enabled and the task isn't done). The opaque hit behavior keeps the tap
    // from falling through to the card's open-detail gesture.
    if (widget.onTitleChanged == null || isDone) return text;
    return GestureDetector(
      key: ValueKey('task-row-title-${widget.task.id}'),
      behavior: HitTestBehavior.opaque,
      onTap: _beginEditTitle,
      child: text,
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
      decoration: BoxDecoration(color: color, borderRadius: AppRadii.rLg),
      child: Icon(icon, color: iconColor, size: 24),
    );
  }
}
