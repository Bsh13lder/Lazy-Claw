import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/core/due_date.dart';
import 'package:lazyclaw_mobile/core/recurrence.dart';
import 'package:lazyclaw_mobile/core/reminder_lead.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../models/project.dart';
import '../../models/subtask.dart';
import '../../models/task.dart';
import '../../providers/tasks_provider.dart';
import '../expenses/project_color_picker.dart';
import '../settings/settings_prefs.dart';
import 'chip_edit.dart';
import 'recurrence_picker.dart';
import 'reminder_lead_picker.dart';
import 'reschedule_sheet.dart';
import 'subtask_editor.dart';

/// A task detail/edit bottom sheet. Pre-fills every field from [task] and lets
/// the user change the title, notes, priority, project and due date, then Save
/// (patch via [TasksNotifier.updateTask]) or Delete (confirm, then
/// [TasksNotifier.deleteTask]). Mirrors the add-task sheet's look so the two
/// surfaces feel like one family.
class TaskDetailSheet extends ConsumerStatefulWidget {
  const TaskDetailSheet({
    super.key,
    required this.task,
    this.projects = const [],
    this.defaultLead = kDefaultReminderLead,
  });

  final Task task;

  /// Known projects (name + color) for the project picker. Empty when the
  /// caller doesn't surface project editing.
  final List<Project> projects;

  /// Global default reminder lead, applied when the task has a due time but no
  /// explicit reminder yet (mirrors the add-task sheet).
  final ReminderLead defaultLead;

  @override
  ConsumerState<TaskDetailSheet> createState() => _TaskDetailSheetState();
}

class _TaskDetailSheetState extends ConsumerState<TaskDetailSheet> {
  late final TextEditingController _titleController;
  late final TextEditingController _notesController;
  late String _priority;

  /// The due date is split into a date-only day string (`_dueDay`) and a
  /// separate time-of-day (`_dueTime`), pre-filled from the task's stored
  /// dueDate (which may be date-only or a full ISO datetime).
  String? _dueDay;
  TimeOfDay? _dueTime;
  bool _saving = false;
  bool _deleting = false;

  /// The user's explicit reminder-lead choice. Seeded from the task's existing
  /// reminderAt; null when the task has no reminder yet, so the global default
  /// applies once a due time is present.
  ReminderLead? _explicitLead;

  /// Working copy of the task's sub-tasks. Edited in-sheet and committed as part
  /// of Save (one atomic [TasksNotifier.updateTask] call). [_originalSteps]
  /// snapshots the on-open serialization so Save only writes `steps` when the
  /// checklist actually changed (no churn on a title-only edit).
  late List<Subtask> _subtasks;
  String? _originalSteps;

  /// The selected project (`category`). Seeded from the task; null/blank means
  /// "No project". [_categoryTouched] gates whether Save writes the column, so a
  /// title-only edit never churns the category.
  String? _category;
  bool _categoryTouched = false;

  /// The selected recurrence. Seeded from the task's stored cron via
  /// [recurrenceFromCron]. [_recurrenceTouched] gates whether Save writes the
  /// `recurring` column, so a title-only edit never churns it (and a non-editable
  /// "custom" cron is preserved untouched until the user picks a known kind).
  late Recurrence _recurrence;
  bool _recurrenceTouched = false;

  static const _priorities = ['low', 'medium', 'high', 'urgent'];

  @override
  void initState() {
    super.initState();
    final t = widget.task;
    _titleController = TextEditingController(text: t.title);
    _notesController = TextEditingController(text: t.description ?? '');
    _priority = _priorities.contains(t.priority) ? t.priority : 'medium';
    final raw = t.dueDate;
    _dueDay = (raw == null || raw.isEmpty) ? null : dueDateDayPart(raw);
    final parts = dueTimeParts(raw);
    _dueTime = parts == null
        ? null
        : TimeOfDay(hour: parts.hour, minute: parts.minute);
    final hasReminder = t.reminderAt != null && t.reminderAt!.isNotEmpty;
    _explicitLead = hasReminder ? leadFromReminderAt(raw, t.reminderAt) : null;
    _subtasks = List.of(t.subtasks);
    _originalSteps = serializeSubtasks(_subtasks);
    _category = (t.category == null || t.category!.isEmpty) ? null : t.category;
    _recurrence = recurrenceFromCron(t.recurring);
  }

  /// The effective reminder lead (explicit choice wins over the global default).
  ReminderLead get _effectiveLead => _explicitLead ?? widget.defaultLead;

  /// The reminderAt string to persist: `''` (clear) when there's no due time or
  /// the lead is None, else the absolute `due − lead` instant.
  String get _composedReminderAt => resolveReminderAt(
    dueDate: _composedDue,
    explicitLead: _explicitLead,
    defaultLead: widget.defaultLead,
  );

  /// Combine the day + time into the persisted `dueDate` string: a datetime when
  /// a time is set, a date-only string when only a day is set, today+time when
  /// only a time is set, else null.
  String? get _composedDue {
    final day = _dueDay;
    final time = _dueTime;
    if (day == null) {
      if (time == null) return null;
      final n = DateTime.now();
      return composeDueDate(
        DateTime(n.year, n.month, n.day),
        hour: time.hour,
        minute: time.minute,
      );
    }
    final d = DateTime.tryParse(day);
    if (d == null) return day;
    return composeDueDate(
      DateTime(d.year, d.month, d.day),
      hour: time?.hour,
      minute: time?.minute,
    );
  }

  @override
  void dispose() {
    _titleController.dispose();
    _notesController.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final title = _titleController.text.trim();
    if (title.isEmpty || _saving || _deleting) return;
    setState(() => _saving = true);
    // Only write `steps` when the checklist changed. An empty string (not null)
    // force-clears the column when every sub-task was removed.
    final nextSteps = serializeSubtasks(_subtasks);
    final stepsArg = nextSteps == _originalSteps ? null : (nextSteps ?? '');
    // Only write `category` when the user touched the project. An empty string
    // (not null) force-clears the column when "No project" was chosen.
    final categoryArg = !_categoryTouched ? null : (_category ?? '');
    // Only write `recurring` when the user touched the repeat picker, so an
    // untouched custom/known cron is preserved. A "does not repeat" selection
    // sends an empty string to force-clear the column; otherwise the computed
    // cron (anchored to the due date) is sent.
    final recurringArg = !_recurrenceTouched
        ? null
        : (recurrenceToCron(
                _recurrence,
                dueAnchor: recurrenceAnchorFromDue(_composedDue),
              ) ??
              '');
    await ref
        .read(tasksProvider.notifier)
        .updateTask(
          widget.task.id,
          title: title,
          description: _notesController.text.trim(),
          priority: _priority,
          category: categoryArg,
          // Send the `''` clear sentinel (not null) when the due date was
          // removed, so the clear reaches the cache + outbox and syncs — a null
          // is read as "field untouched" and would silently no-op the clear.
          dueDate: _composedDue ?? '',
          steps: stepsArg,
          reminderAt: _composedReminderAt,
          recurring: recurringArg,
        );
    if (!mounted) return;
    Navigator.of(context).pop();
  }

  /// Close this sheet and open the Smart Fast Reschedule sheet for the task.
  /// The reschedule writes through [TasksNotifier.updateTask] itself, so we just
  /// hand off — any in-progress edits here are intentionally discarded (a quick
  /// reschedule is a deliberate "just move the date" action).
  Future<void> _reschedule() async {
    final navigator = Navigator.of(context);
    // Use the navigator's (still-mounted) context to present the next sheet —
    // this sheet's own context becomes defunct once we pop it below.
    final rootContext = navigator.context;
    final task = widget.task;
    navigator.pop();
    await showRescheduleSheet(rootContext, ref, task);
  }

  Future<void> _pickProject() async {
    final result = await showProjectPicker(
      context,
      projects: widget.projects,
      current: _category,
    );
    if (result == null || !mounted) return;
    setState(() {
      _categoryTouched = true;
      _category = (result.category == null || result.category!.isEmpty)
          ? null
          : result.category;
    });
  }

  Future<void> _delete() async {
    if (_saving || _deleting) return;
    final confirmed = await LzConfirm.show(
      context,
      title: 'Delete task?',
      message: widget.task.title,
      confirmLabel: 'Delete',
      danger: true,
    );
    if (!confirmed || !mounted) return;
    setState(() => _deleting = true);
    await ref.read(tasksProvider.notifier).deleteTask(widget.task.id);
    if (!mounted) return;
    Navigator.of(context).pop();
  }

  Color _priorityColor(String p) {
    switch (p) {
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

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // ── Title ──────────────────────────────────────────────────────
          LzTextField(
            controller: _titleController,
            fieldKey: const Key('task-detail-title'),
            hint: 'What needs to be done?',
            prefixIcon: Icons.task_alt_outlined,
            textInputAction: TextInputAction.next,
          ),

          const SizedBox(height: AppSpacing.lg),

          // ── Notes ──────────────────────────────────────────────────────
          LzTextField(
            controller: _notesController,
            fieldKey: const Key('task-detail-notes'),
            hint: 'Notes (optional)',
            prefixIcon: Icons.notes_outlined,
            minLines: 2,
            maxLines: 5,
            keyboardType: TextInputType.multiline,
          ),

          const SizedBox(height: AppSpacing.xl),

          // ── Priority selector ──────────────────────────────────────────
          _SectionLabel('PRIORITY'),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: _priorities.map((p) {
              return Padding(
                padding: const EdgeInsets.only(right: AppSpacing.sm),
                child: LzChip(
                  label: p,
                  selected: p == _priority,
                  color: _priorityColor(p),
                  onTap: () => setState(() => _priority = p),
                ),
              );
            }).toList(),
          ),

          const SizedBox(height: AppSpacing.xl),

          // ── Project selector ───────────────────────────────────────────
          _SectionLabel('PROJECT'),
          const SizedBox(height: AppSpacing.sm),
          Align(
            alignment: Alignment.centerLeft,
            child: _ProjectChip(
              projects: widget.projects,
              category: _category,
              onTap: _pickProject,
            ),
          ),

          const SizedBox(height: AppSpacing.xl),

          // ── Due date quick-pick ────────────────────────────────────────
          Row(
            children: [
              _SectionLabel('DUE DATE'),
              const Spacer(),
              LzButton.ghost(
                key: const Key('task-detail-reschedule'),
                label: 'Reschedule',
                icon: Icons.event_repeat_outlined,
                onPressed: _reschedule,
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              LzChip(
                label: 'Today',
                icon: Icons.today_outlined,
                selected: _dueDay == _isoToday(),
                color: AppColors.warn,
                onTap: () => setState(() {
                  _dueDay = _dueDay == _isoToday() ? null : _isoToday();
                }),
              ),
              const SizedBox(width: AppSpacing.sm),
              LzChip(
                label: 'Tomorrow',
                icon: Icons.event_outlined,
                selected: _dueDay == _isoTomorrow(),
                color: AppColors.accent,
                onTap: () => setState(() {
                  _dueDay = _dueDay == _isoTomorrow() ? null : _isoTomorrow();
                }),
              ),
              const SizedBox(width: AppSpacing.sm),
              LzChip(
                label: 'Pick…',
                icon: Icons.calendar_month_outlined,
                selected:
                    _dueDay != null &&
                    _dueDay != _isoToday() &&
                    _dueDay != _isoTomorrow(),
                color: AppColors.info,
                onTap: _pickDate,
              ),
            ],
          ),

          // ── Time-of-day chip ──────────────────────────────────────────
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              LzChip(
                label: _dueTime != null
                    ? formatClock12(_dueTime!.hour, _dueTime!.minute)
                    : 'Add time',
                icon: Icons.schedule_outlined,
                selected: _dueTime != null,
                color: AppColors.accent,
                onTap: _pickTime,
              ),
              if (_dueTime != null) ...[
                const SizedBox(width: AppSpacing.sm),
                GestureDetector(
                  onTap: () => setState(() => _dueTime = null),
                  child: Icon(
                    Icons.close,
                    size: 16,
                    color: AppColors.textMuted,
                  ),
                ),
              ],
            ],
          ),

          if (_composedDue != null) ...[
            const SizedBox(height: AppSpacing.sm),
            Row(
              children: [
                Icon(
                  Icons.event_available_outlined,
                  size: 14,
                  color: AppColors.textMuted,
                ),
                const SizedBox(width: AppSpacing.xs),
                Text(
                  'Due ${dueDateDisplay(_composedDue!)}',
                  style: AppText.caption.copyWith(
                    color: AppColors.textSecondary,
                  ),
                ),
                const Spacer(),
                GestureDetector(
                  onTap: () => setState(() {
                    _dueDay = null;
                    _dueTime = null;
                  }),
                  child: Icon(
                    Icons.close,
                    size: 14,
                    color: AppColors.textMuted,
                  ),
                ),
              ],
            ),
          ],

          // ── Reminder lead-time (only when a due TIME exists) ───────────
          if (dueDateHasTime(_composedDue)) ...[
            const SizedBox(height: AppSpacing.xl),
            _SectionLabel('REMIND'),
            const SizedBox(height: AppSpacing.sm),
            ReminderLeadPicker(
              value: _effectiveLead,
              onChanged: (lead) => setState(() => _explicitLead = lead),
            ),
          ],

          // ── Recurrence (repeat) ────────────────────────────────────────
          const SizedBox(height: AppSpacing.xl),
          _SectionLabel('REPEAT'),
          const SizedBox(height: AppSpacing.sm),
          RecurrencePicker(
            value: _recurrence,
            anchorWeekday: _composedDue == null
                ? null
                : DateTime.tryParse(_composedDue!)?.weekday,
            onChanged: (r) => setState(() {
              _recurrenceTouched = true;
              _recurrence = r;
            }),
          ),

          const SizedBox(height: AppSpacing.xl),

          // ── Sub-tasks ──────────────────────────────────────────────────
          Row(
            children: [
              _SectionLabel('SUBTASKS'),
              const Spacer(),
              if (subtaskProgressLabel(_subtasks) != null)
                Text(
                  subtaskProgressLabel(_subtasks)!,
                  key: const Key('task-detail-subtask-progress'),
                  style: AppText.caption.copyWith(color: AppColors.accent),
                ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          SubtaskEditor(
            subtasks: _subtasks,
            onChanged: (next) => setState(() => _subtasks = next),
          ),

          const SizedBox(height: AppSpacing.xxl),

          // ── Footer: Delete + Save ──────────────────────────────────────
          Row(
            children: [
              LzButton.danger(
                key: const Key('task-detail-delete'),
                label: 'Delete Task',
                icon: Icons.delete_outline,
                loading: _deleting,
                onPressed: _delete,
              ),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: LzButton.primary(
                  key: const Key('task-detail-save'),
                  label: 'Save',
                  icon: Icons.check,
                  loading: _saving,
                  expand: true,
                  onPressed: _save,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Future<void> _pickDate() async {
    final now = DateTime.now();
    DateTime initial = now.add(const Duration(days: 1));
    if (_dueDay != null) {
      try {
        final parsed = DateTime.parse(_dueDay!);
        if (!parsed.isBefore(DateTime(now.year, now.month, now.day))) {
          initial = parsed;
        }
      } catch (_) {
        // Keep the default when the stored value isn't ISO-parseable.
      }
    }
    final picked = await showDatePicker(
      context: context,
      initialDate: initial,
      firstDate: now.subtract(const Duration(days: 365)),
      lastDate: now.add(const Duration(days: 365)),
      builder: (ctx, child) => Theme(
        data: Theme.of(ctx).copyWith(
          colorScheme: ColorScheme.dark(
            primary: AppColors.accent,
            surface: AppColors.bgSurfaceElevated,
          ),
        ),
        child: child!,
      ),
    );
    if (picked != null) {
      setState(() => _dueDay = _isoFor(picked));
    }
  }

  Future<void> _pickTime() async {
    final picked = await showTimePicker(
      context: context,
      initialTime: _dueTime ?? const TimeOfDay(hour: 9, minute: 0),
      builder: (ctx, child) => Theme(
        data: Theme.of(ctx).copyWith(
          colorScheme: ColorScheme.dark(
            primary: AppColors.accent,
            surface: AppColors.bgSurfaceElevated,
          ),
        ),
        child: child!,
      ),
    );
    if (picked != null) setState(() => _dueTime = picked);
  }

  String _isoFor(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  String _isoToday() => _isoFor(DateTime.now());

  String _isoTomorrow() => _isoFor(DateTime.now().add(const Duration(days: 1)));
}

/// A small uppercase section label matching the add-task sheet's headers.
class _SectionLabel extends StatelessWidget {
  const _SectionLabel(this.text);
  final String text;

  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      style: AppText.caption.copyWith(
        color: AppColors.textMuted,
        letterSpacing: 0.8,
        fontWeight: FontWeight.w700,
      ),
    );
  }
}

/// The tappable project control: a color dot + project name (or "No project"),
/// opening the project picker.
class _ProjectChip extends StatelessWidget {
  const _ProjectChip({
    required this.projects,
    required this.category,
    required this.onTap,
  });

  final List<Project> projects;
  final String? category;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final hasCategory = category != null && category!.isNotEmpty;
    String? colorHex;
    if (hasCategory) {
      for (final p in projects) {
        if (p.name.toLowerCase() == category!.toLowerCase()) {
          colorHex = p.color;
          break;
        }
      }
    }

    return Material(
      color: Colors.transparent,
      borderRadius: AppRadii.rPill,
      child: InkWell(
        key: const Key('task-detail-project'),
        onTap: onTap,
        borderRadius: AppRadii.rPill,
        child: Container(
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.md,
            vertical: AppSpacing.xs + 2,
          ),
          decoration: BoxDecoration(
            color: AppColors.bgSurfaceElevated,
            borderRadius: AppRadii.rPill,
            border: Border.all(color: AppColors.borderDefault),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (hasCategory)
                ProjectColorDot(hex: colorHex, size: 12)
              else
                Icon(
                  Icons.folder_outlined,
                  size: 15,
                  color: AppColors.textMuted,
                ),
              const SizedBox(width: AppSpacing.sm),
              Text(
                hasCategory ? category! : 'No project',
                style: AppText.caption.copyWith(
                  color: hasCategory
                      ? AppColors.textPrimary
                      : AppColors.textMuted,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              Icon(Icons.expand_more, size: 16, color: AppColors.textMuted),
            ],
          ),
        ),
      ),
    );
  }
}

// ── Public helper ─────────────────────────────────────────────────────────────

/// Open the task detail/edit sheet for [task]. The sheet reads
/// [tasksProvider] for Save/Delete; [ref] is accepted so the call site (the
/// Tasks screen, which already holds a [WidgetRef]) owns the invocation.
/// [projects] populates the project picker.
Future<void> showTaskDetailSheet(
  BuildContext context,
  WidgetRef ref,
  Task task, {
  List<Project> projects = const [],
  ReminderLead defaultLead = kDefaultReminderLead,
}) {
  return LzBottomSheet.show<void>(
    context,
    title: 'Edit Task',
    builder: (_) => TaskDetailSheet(
      task: task,
      projects: projects,
      defaultLead: defaultLead,
    ),
  );
}
