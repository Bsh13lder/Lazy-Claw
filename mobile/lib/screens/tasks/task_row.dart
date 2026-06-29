import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:lazyclaw_mobile/core/due_date.dart';
import 'package:lazyclaw_mobile/core/recurrence.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../models/project.dart';
import '../../models/subtask.dart';
import '../../models/task.dart';
import 'chip_edit.dart';
import 'task_calendar_utils.dart';
import 'subtask_editor.dart';

/// A full task card: a checkbox affordance, an (optionally inline-editable)
/// title, a row of chips (project · priority · due date · time · subtask
/// progress), and a sync badge. Wrapped in a [Dismissible] so the parent list
/// gets swipe-to-complete (startToEnd) and swipe-to-delete (endToStart).
///
/// ## Tap model (Todoist/Taskade-style)
/// * **Tap the title text** → inline-edit the title (when [onTitleChanged] is
///   set); commits on submit or blur.
/// * **Tap a chip** → quick-edit that one field in place (when its callback is
///   wired): the **priority** chip opens a Low/Medium/High/Urgent menu, the
///   **due-date** chip opens a date picker (time preserved), the **time** tag
///   opens a time picker (day preserved), and the **project** chip opens a
///   project picker. A project-less task shows a muted "Project" chip that opens
///   the same picker.
/// * **Tap the subtask chip** → fold/unfold the checklist inline (folded by
///   default). Expanded, each row toggles done / inline-edits like the detail
///   sheet.
/// * **Single tap elsewhere on the card** → [onTap] (open the full detail /
///   settings sheet), instantly.
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
    this.projects = const [],
    this.onPriorityChanged,
    this.onDueDateChanged,
    this.onCategoryChanged,
    this.onSubtasksChanged,
    this.onReschedule,
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

  /// Known projects (name + color) for the tap-the-chip project picker. Empty
  /// when the caller doesn't surface project editing.
  final List<Project> projects;

  /// Commits a tap-the-chip priority change. Null → the priority chip is a
  /// static label (no menu).
  final ValueChanged<String>? onPriorityChanged;

  /// Commits a tap-the-chip due-date / time change (the full recomposed
  /// `dueDate` string). Null → the date/time chips are static labels.
  final ValueChanged<String>? onDueDateChanged;

  /// Commits a tap-the-chip project change. An empty string clears the project.
  /// Null → the project chip is a static label and no "add project" affordance
  /// is shown.
  final ValueChanged<String>? onCategoryChanged;

  /// Commits an inline subtask checklist edit (the full new list). Null → the
  /// subtask chip is a static progress badge (no fold/checklist).
  final ValueChanged<List<Subtask>>? onSubtasksChanged;

  /// Opens the Smart Fast Reschedule sheet (the screen owns the [WidgetRef]).
  /// When set AND the task is overdue, tapping the due chip routes here instead
  /// of the bare date picker. Null → overdue cards keep the plain date picker.
  final VoidCallback? onReschedule;

  @override
  State<TaskRow> createState() => _TaskRowState();
}

class _TaskRowState extends State<TaskRow> {
  late final TextEditingController _titleCtrl;
  late final FocusNode _titleFocus;
  bool _editingTitle = false;

  /// Whether the inline subtask checklist is unfolded. Folded by default.
  bool _subtasksExpanded = false;

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

  // ── Tap-the-chip quick edits ───────────────────────────────────────────────

  Future<void> _editPriority(BuildContext chipCtx) async {
    final cb = widget.onPriorityChanged;
    if (cb == null) return;
    final picked = await showPriorityMenu(
      chipCtx,
      current: widget.task.priority,
    );
    if (picked == null || picked == widget.task.priority) return;
    HapticFeedback.selectionClick();
    cb(picked);
  }

  /// True when the task's due day is strictly before today (the overdue
  /// section). Overdue cards route their due chip to the reschedule sheet.
  bool get _isOverdue {
    final raw = widget.task.dueDate;
    if (raw == null || widget.task.isDone) return false;
    final parsed = DateTime.tryParse(raw);
    if (parsed == null) return false;
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final dueDay = DateTime(parsed.year, parsed.month, parsed.day);
    return dueDay.isBefore(today);
  }

  /// The due-chip tap handler: the reschedule sheet for overdue cards (when an
  /// [TaskRow.onReschedule] is wired), else the plain date picker.
  VoidCallback? get _onDueChipTap {
    if (_isOverdue && widget.onReschedule != null) return widget.onReschedule;
    return widget.onDueDateChanged != null ? _editDueDate : null;
  }

  Future<void> _editDueDate() async {
    final cb = widget.onDueDateChanged;
    if (cb == null) return;
    final raw = widget.task.dueDate;
    DateTime? initial;
    if (raw != null) {
      final parsed = DateTime.tryParse(raw);
      if (parsed != null) {
        initial = DateTime(parsed.year, parsed.month, parsed.day);
      }
    }
    final picked = await showThemedDatePicker(context, initial: initial);
    if (picked == null) return;
    HapticFeedback.selectionClick();
    cb(withNewDueDay(raw, picked));
  }

  Future<void> _editDueTime() async {
    final cb = widget.onDueDateChanged;
    final raw = widget.task.dueDate;
    if (cb == null || raw == null) return;
    final parts = dueTimeParts(raw);
    final initial = parts == null
        ? null
        : TimeOfDay(hour: parts.hour, minute: parts.minute);
    final picked = await showThemedTimePicker(context, initial: initial);
    if (picked == null) return;
    HapticFeedback.selectionClick();
    cb(withNewDueTime(raw, picked.hour, picked.minute));
  }

  Future<void> _editProject() async {
    final cb = widget.onCategoryChanged;
    if (cb == null) return;
    final result = await showProjectPicker(
      context,
      projects: widget.projects,
      current: widget.task.category,
    );
    if (result == null) return; // dismissed without a choice
    HapticFeedback.selectionClick();
    cb(result.category ?? '');
  }

  @override
  Widget build(BuildContext context) {
    final task = widget.task;
    final isDone = task.isDone;
    final pColor = priorityColor(task.priority);

    final hasCategory = task.category != null && task.category!.isNotEmpty;
    // Tie the project chip to the project's own color (the same hue the calendar
    // dots use), falling back to the neutral "info" tone when the project has no
    // color or isn't in the list.
    final projectColor = hasCategory ? _projectColor(task.category!) : null;
    final progress = subtaskProgressLabel(task.subtasks);
    final hasSubtasks = progress != null;
    // The time tag's label: the due date's own time, or the reminderAt time on
    // a date-only due (the Smart-Reschedule 10:00 AM default). Null = no time.
    final cardTimeLabel = cardDueTimeLabel(task.dueDate, task.reminderAt);
    // A subtle "🔁 <label>" chip when the task repeats (the recurrence cron
    // parses to a known kind, or a generic "Repeats" for a custom cron).
    final recurLabel = cronChipLabel(task.recurring);
    final subtasksEditable = widget.onSubtasksChanged != null;
    final showChecklist = hasSubtasks && subtasksEditable && _subtasksExpanded;

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
                    if (hasCategory)
                      LzChip(
                        key: ValueKey('task-row-project-${task.id}'),
                        label: task.category!,
                        dense: true,
                        icon: Icons.folder_outlined,
                        color: projectColor ?? AppColors.info,
                        selected: !isDone,
                        onTap: widget.onCategoryChanged != null
                            ? _editProject
                            : null,
                      )
                    else if (widget.onCategoryChanged != null)
                      // No project yet → a muted "add project" affordance.
                      LzChip(
                        key: ValueKey('task-row-project-add-${task.id}'),
                        label: 'Project',
                        dense: true,
                        icon: Icons.create_new_folder_outlined,
                        color: AppColors.textMuted,
                        onTap: _editProject,
                      ),
                    // Priority chip — always visible. Wrapped in a Builder so
                    // its quick-edit menu anchors to the chip itself.
                    Builder(
                      builder: (chipCtx) => LzChip(
                        key: ValueKey('task-row-priority-${task.id}'),
                        label: task.priority,
                        dense: true,
                        color: pColor,
                        selected: !isDone,
                        onTap: widget.onPriorityChanged != null
                            ? () => _editPriority(chipCtx)
                            : null,
                      ),
                    ),
                    // Due date chip (date only — the time gets its own tag).
                    if (task.dueDate != null)
                      LzChip(
                        key: ValueKey('task-row-due-${task.id}'),
                        label: dueDateDayPart(task.dueDate!),
                        dense: true,
                        icon: Icons.calendar_today_outlined,
                        color: _dueDateColor(task.dueDate!),
                        selected: !isDone,
                        onTap: _onDueChipTap,
                      ),
                    // Time-of-day tag — shown when the due date carries a time,
                    // OR when it's date-only but a reminderAt time exists (the
                    // Smart-Reschedule 10:00 AM default), so the time is visible.
                    if (cardTimeLabel != null)
                      LzChip(
                        key: ValueKey('task-row-time-${task.id}'),
                        label: cardTimeLabel,
                        dense: true,
                        icon: Icons.schedule_outlined,
                        color: _dueDateColor(task.dueDate!),
                        selected: !isDone,
                        // Date-only + reminder time → tapping reschedules;
                        // an own-time chip edits the time in place as before.
                        onTap: dueDateHasTime(task.dueDate)
                            ? (widget.onDueDateChanged != null
                                  ? _editDueTime
                                  : null)
                            : _onDueChipTap,
                      ),
                    // Recurrence chip — subtle, static (editing happens in the
                    // detail sheet's REPEAT picker).
                    if (recurLabel != null)
                      LzChip(
                        key: ValueKey('task-row-recurrence-${task.id}'),
                        label: recurLabel,
                        dense: true,
                        icon: Icons.repeat,
                        color: AppColors.info,
                        selected: !isDone,
                      ),
                    // Subtask progress — a fold toggle when editable, else a
                    // static badge.
                    if (hasSubtasks && subtasksEditable)
                      LzChip(
                        key: ValueKey('task-row-subtasks-${task.id}'),
                        label: progress,
                        dense: true,
                        icon: _subtasksExpanded
                            ? Icons.keyboard_arrow_down
                            : Icons.chevron_right,
                        color: AppColors.accent,
                        selected: _subtasksExpanded && !isDone,
                        onTap: () {
                          HapticFeedback.selectionClick();
                          setState(
                            () => _subtasksExpanded = !_subtasksExpanded,
                          );
                        },
                      )
                    else if (hasSubtasks)
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

                // ── Inline checklist (folded by default) ───────────────────
                if (showChecklist) ...[
                  const SizedBox(height: AppSpacing.sm),
                  Padding(
                    key: ValueKey('task-row-checklist-${task.id}'),
                    padding: const EdgeInsets.only(right: AppSpacing.sm),
                    child: SubtaskEditor(
                      subtasks: task.subtasks,
                      onChanged: widget.onSubtasksChanged!,
                    ),
                  ),
                ],
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
          // A touch heavier than the caption-sized chips so the title clearly
          // leads the card's hierarchy.
          : AppText.body.copyWith(fontWeight: FontWeight.w600),
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

  /// The project's own color for [category] (case-insensitive name match), or
  /// null when there's no matching project or it has no/invalid color.
  Color? _projectColor(String category) {
    final target = category.toLowerCase();
    for (final p in widget.projects) {
      if (p.name.toLowerCase() != target) continue;
      final hex = p.color;
      if (hex == null || hex.isEmpty) return null;
      return parseHexColor(hex);
    }
    return null;
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
