import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/core/due_date.dart';
import 'package:lazyclaw_mobile/core/reminder_lead.dart';
import 'package:lazyclaw_mobile/core/smart_add_parser.dart';
import 'package:lazyclaw_mobile/screens/settings/settings_prefs.dart';
import 'package:lazyclaw_mobile/screens/tasks/reminder_lead_picker.dart';
import 'package:lazyclaw_mobile/screens/tasks/smart_add_controller.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

/// A polished add-task bottom sheet with Todoist-style "smart add": as the user
/// types, recognized natural-language tokens (due date, priority, `#project`)
/// are parsed on-device and surfaced as chips. Parsed values pre-select the
/// priority / due-date controls; manual taps always win.
///
/// Returns the data to the caller via [Navigator.pop] so the screen can invoke
/// the provider without knowing about UI internals.
class AddTaskSheet extends ConsumerStatefulWidget {
  const AddTaskSheet({
    super.key,
    this.initialDueDate,
    this.defaultLead = kDefaultReminderLead,
  });

  /// When provided (e.g. tapping a day in the calendar view), the due date is
  /// pre-selected to this day so the new task lands on the chosen date.
  final DateTime? initialDueDate;

  /// The global default reminder lead, applied automatically once the task
  /// gains a due time and the user hasn't picked a lead explicitly.
  final ReminderLead defaultLead;

  @override
  ConsumerState<AddTaskSheet> createState() => _AddTaskSheetState();
}

class _AddTaskSheetState extends ConsumerState<AddTaskSheet> {
  final _titleController = SmartAddController();

  /// Live parse of the current title text. Drives the detected-token chips and
  /// the default selections for priority / due date.
  ParsedTask _parsed = const ParsedTask(cleanTitle: '');

  /// Manual overrides. When set, they win over the parsed values. The due date
  /// is split into a date-only day string (`_manualDueDate`) and a separate
  /// time-of-day (`_manualTime`) so the two pickers are independent.
  String? _manualPriority;
  bool _dueDateTouched = false;
  String? _manualDueDate;
  bool _timeTouched = false;
  TimeOfDay? _manualTime;

  /// The user's explicit reminder-lead choice. Null until they touch the
  /// picker, so the global default applies automatically once a time is set.
  ReminderLead? _explicitLead;

  bool _submitting = false;

  static const _priorities = ['low', 'medium', 'high', 'urgent'];

  @override
  void initState() {
    super.initState();
    // A caller-supplied date (calendar day-tap) pre-selects the due date. We
    // mark the field as touched so it wins over any parsed default and stays
    // put until the user explicitly changes it.
    final initial = widget.initialDueDate;
    if (initial != null) {
      _dueDateTouched = true;
      _manualDueDate = _isoFor(initial);
    }
  }

  @override
  void dispose() {
    _titleController.dispose();
    super.dispose();
  }

  // ── Effective (override-aware) values ───────────────────────────────────────

  String get _effectivePriority =>
      _manualPriority ?? _parsed.priority ?? 'medium';

  /// The parsed due date's date-only day part (`yyyy-MM-dd`), or null.
  String? get _parsedDay {
    final due = _parsed.dueDate;
    return due == null ? null : dueDateDayPart(due);
  }

  /// The parsed due date's time-of-day, or null when it's date-only.
  TimeOfDay? get _parsedTime {
    final parts = dueTimeParts(_parsed.dueDate);
    return parts == null ? null : TimeOfDay(hour: parts.hour, minute: parts.minute);
  }

  /// The effective day (manual override wins over the live parse).
  String? get _effectiveDay => _dueDateTouched ? _manualDueDate : _parsedDay;

  /// The effective time (manual override wins over the live parse).
  TimeOfDay? get _effectiveTime => _timeTouched ? _manualTime : _parsedTime;

  /// The effective reminder lead (explicit choice wins over the global default).
  ReminderLead get _effectiveLead => _explicitLead ?? widget.defaultLead;

  bool get _hasDetection =>
      _parsed.dueDate != null ||
      _parsed.priority != null ||
      _parsed.project != null;

  /// Combine a date-only [day] string with an optional [time] into the final
  /// `dueDate` payload: a datetime when a time is set, a date-only string when
  /// only a day is set, today+time when only a time is set, else null.
  String? _compose(String? day, TimeOfDay? time) {
    if (day == null) {
      if (time == null) return null;
      final n = DateTime.now();
      return composeDueDate(DateTime(n.year, n.month, n.day),
          hour: time.hour, minute: time.minute);
    }
    final d = DateTime.tryParse(day);
    if (d == null) return day; // non-ISO fallback: leave as-is
    return composeDueDate(DateTime(d.year, d.month, d.day),
        hour: time?.hour, minute: time?.minute);
  }

  void _onTitleChanged(String value) {
    final parsed = parseSmartAdd(value);
    // Push the fresh spans into the controller so the field highlights the
    // recognized tokens live; the chips below echo the resolved values.
    _titleController.tokens = parsed.tokens;
    setState(() => _parsed = parsed);
  }

  Future<void> _submit() async {
    // Re-parse from the live controller so a submit-via-keyboard can't race the
    // onChanged callback.
    final parsed = parseSmartAdd(_titleController.text);
    final clean = parsed.cleanTitle.trim();
    final title = clean.isNotEmpty ? clean : _titleController.text.trim();
    if (title.isEmpty) return;

    final priority = _manualPriority ?? parsed.priority ?? 'medium';

    // Re-derive the effective day/time from this fresh parse so a keyboard
    // submit can't race the onChanged callback, then compose them.
    final parsedDay = parsed.dueDate == null ? null : dueDateDayPart(parsed.dueDate!);
    final pt = dueTimeParts(parsed.dueDate);
    final parsedTime =
        pt == null ? null : TimeOfDay(hour: pt.hour, minute: pt.minute);
    final day = _dueDateTouched ? _manualDueDate : parsedDay;
    final time = _timeTouched ? _manualTime : parsedTime;
    final dueDate = _compose(day, time);

    // Compute the absolute reminderAt from the effective lead. Empty when there
    // is no due time or the lead is None — addTask normalises that to "no
    // reminder".
    final reminderAt = resolveReminderAt(
      dueDate: dueDate,
      explicitLead: _explicitLead,
      defaultLead: widget.defaultLead,
    );

    setState(() => _submitting = true);
    Navigator.of(context).pop(_AddTaskResult(
      title: title,
      priority: priority,
      dueDate: dueDate,
      category: parsed.project,
      reminderAt: reminderAt.isEmpty ? null : reminderAt,
    ));
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
    final effDay = _effectiveDay;
    final effTime = _effectiveTime;
    final effPriority = _effectivePriority;
    final composed = _compose(effDay, effTime);

    // The configured time-of-day at which DATE-ONLY tasks remind (falls back to
    // the built-in default until the prefs have loaded). Used only to LABEL the
    // muted "Reminds at …" hint below — the actual scheduling reads the same
    // pref in the reminder service.
    final defaultReminderMinutes = ref
            .watch(settingsPrefsProvider)
            .valueOrNull
            ?.defaultReminderMinutes ??
        kDefaultReminderMinutes;

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // ── Title input ────────────────────────────────────────────────
        LzTextField(
          controller: _titleController,
          hint: 'e.g. "Pay rent tomorrow !p1 #home"',
          prefixIcon: Icons.task_alt_outlined,
          textInputAction: TextInputAction.done,
          onChanged: _onTitleChanged,
          onSubmitted: (_) => _submit(),
          autofocus: true,
        ),

        // ── Syntax legend (discoverability) ────────────────────────────
        const SizedBox(height: AppSpacing.xs),
        Text(
          'tom · fri 9am · in 2h · morning · !p1 · #project',
          style: AppText.caption.copyWith(color: AppColors.textMuted),
        ),

        // ── Smart-detected tokens (live) ───────────────────────────────
        if (_hasDetection) ...[
          const SizedBox(height: AppSpacing.md),
          Row(
            children: [
              Icon(Icons.auto_awesome_outlined,
                  size: 13, color: AppColors.accent),
              const SizedBox(width: AppSpacing.xs),
              Text(
                'SMART DETECTED',
                style: AppText.caption.copyWith(
                  color: AppColors.accent,
                  letterSpacing: 0.8,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          Wrap(
            spacing: AppSpacing.sm,
            runSpacing: AppSpacing.sm,
            children: [
              if (_parsed.dueDate != null)
                LzChip(
                  label: dueDateDisplay(_parsed.dueDate!),
                  icon: Icons.event_outlined,
                  selected: true,
                  color: AppColors.accent,
                  dense: true,
                ),
              if (_parsed.priority != null)
                LzChip(
                  label: _parsed.priority!,
                  icon: Icons.flag_outlined,
                  selected: true,
                  color: _priorityColor(_parsed.priority!),
                  dense: true,
                ),
              if (_parsed.project != null)
                LzChip(
                  label: '#${_parsed.project!}',
                  icon: Icons.folder_outlined,
                  selected: true,
                  color: AppColors.info,
                  dense: true,
                ),
            ],
          ),
        ],

        const SizedBox(height: AppSpacing.xl),

        // ── Priority selector ──────────────────────────────────────────
        Text(
          'PRIORITY',
          style: AppText.caption.copyWith(
            color: AppColors.textMuted,
            letterSpacing: 0.8,
            fontWeight: FontWeight.w700,
          ),
        ),
        const SizedBox(height: AppSpacing.sm),
        Row(
          children: _priorities.map((p) {
            final selected = p == effPriority;
            return Padding(
              padding: const EdgeInsets.only(right: AppSpacing.sm),
              child: LzChip(
                label: p,
                selected: selected,
                color: _priorityColor(p),
                onTap: () => setState(() => _manualPriority = p),
              ),
            );
          }).toList(),
        ),

        const SizedBox(height: AppSpacing.xl),

        // ── Due date quick-pick ────────────────────────────────────────
        Text(
          'DUE DATE',
          style: AppText.caption.copyWith(
            color: AppColors.textMuted,
            letterSpacing: 0.8,
            fontWeight: FontWeight.w700,
          ),
        ),
        const SizedBox(height: AppSpacing.sm),
        Row(
          children: [
            LzChip(
              label: 'Today',
              icon: Icons.today_outlined,
              selected: effDay == _isoToday(),
              color: AppColors.warn,
              onTap: () => _setDueDate(
                  effDay == _isoToday() ? null : _isoToday()),
            ),
            const SizedBox(width: AppSpacing.sm),
            LzChip(
              label: 'Tomorrow',
              icon: Icons.event_outlined,
              selected: effDay == _isoTomorrow(),
              color: AppColors.accent,
              onTap: () => _setDueDate(
                  effDay == _isoTomorrow() ? null : _isoTomorrow()),
            ),
            const SizedBox(width: AppSpacing.sm),
            LzChip(
              label: 'Pick…',
              icon: Icons.calendar_month_outlined,
              selected: effDay != null &&
                  effDay != _isoToday() &&
                  effDay != _isoTomorrow(),
              color: AppColors.info,
              onTap: _pickDate,
            ),
          ],
        ),

        // ── Time-of-day chip ───────────────────────────────────────────
        const SizedBox(height: AppSpacing.sm),
        Row(
          children: [
            LzChip(
              label: effTime != null
                  ? formatClock12(effTime.hour, effTime.minute)
                  : 'Add time',
              icon: Icons.schedule_outlined,
              selected: effTime != null,
              color: AppColors.accent,
              onTap: _pickTime,
            ),
            if (effTime != null) ...[
              const SizedBox(width: AppSpacing.sm),
              GestureDetector(
                onTap: () => _setTime(null),
                child: Icon(Icons.close, size: 16, color: AppColors.textMuted),
              ),
            ],
          ],
        ),

        if (composed != null) ...[
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              Icon(Icons.event_available_outlined,
                  size: 14, color: AppColors.textMuted),
              const SizedBox(width: AppSpacing.xs),
              Text(
                'Due ${dueDateDisplay(composed)}',
                style: AppText.caption.copyWith(color: AppColors.textSecondary),
              ),
              const Spacer(),
              GestureDetector(
                onTap: _clearDue,
                child: Icon(Icons.close,
                    size: 14, color: AppColors.textMuted),
              ),
            ],
          ),
        ],

        // ── Reminder lead-time (only when a due TIME exists) ───────────
        if (dueDateHasTime(composed)) ...[
          const SizedBox(height: AppSpacing.xl),
          Text(
            'REMIND',
            style: AppText.caption.copyWith(
              color: AppColors.textMuted,
              letterSpacing: 0.8,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          ReminderLeadPicker(
            value: _effectiveLead,
            onChanged: (lead) => setState(() => _explicitLead = lead),
          ),
        ]
        // ── Date-only due → reminds at the default time-of-day ─────────
        // No clock time was set, so this task still gets a reminder (at the
        // configured default time on its due date). Surface that so the user
        // knows the reminder isn't silently skipped.
        else if (composed != null) ...[
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              Icon(Icons.notifications_active_outlined,
                  size: 13, color: AppColors.textMuted),
              const SizedBox(width: AppSpacing.xs),
              Text(
                'Reminds at '
                '${formatClock12(defaultReminderMinutes ~/ 60, defaultReminderMinutes % 60)}'
                ' on the due date',
                style: AppText.caption.copyWith(color: AppColors.textMuted),
              ),
            ],
          ),
        ],

        const SizedBox(height: AppSpacing.xl),

        // ── Submit button ──────────────────────────────────────────────
        LzButton.primary(
          label: 'Add Task',
          icon: Icons.add,
          loading: _submitting,
          expand: true,
          onPressed: _submit,
        ),
      ],
    );
  }

  /// Apply a manual due-date (day) selection (or clear). Marks the field as
  /// touched so the parsed default no longer applies.
  void _setDueDate(String? iso) {
    setState(() {
      _dueDateTouched = true;
      _manualDueDate = iso;
    });
  }

  /// Apply a manual time-of-day selection (or clear). Marks the field as
  /// touched so the parsed default no longer applies.
  void _setTime(TimeOfDay? time) {
    setState(() {
      _timeTouched = true;
      _manualTime = time;
    });
  }

  /// Clear both the day and the time in one tap (the "Due …" summary's ✕).
  void _clearDue() {
    setState(() {
      _dueDateTouched = true;
      _manualDueDate = null;
      _timeTouched = true;
      _manualTime = null;
    });
  }

  Future<void> _pickTime() async {
    final picked = await showTimePicker(
      context: context,
      initialTime: _effectiveTime ?? const TimeOfDay(hour: 9, minute: 0),
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
    if (picked != null) _setTime(picked);
  }

  Future<void> _pickDate() async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: now.add(const Duration(days: 1)),
      firstDate: now,
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
      _setDueDate(
        '${picked.year.toString().padLeft(4, '0')}-${picked.month.toString().padLeft(2, '0')}-${picked.day.toString().padLeft(2, '0')}',
      );
    }
  }

  String _isoFor(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  String _isoToday() => _isoFor(DateTime.now());

  String _isoTomorrow() => _isoFor(DateTime.now().add(const Duration(days: 1)));
}

/// Data returned by [AddTaskSheet] when the user taps "Add Task".
class _AddTaskResult {
  const _AddTaskResult({
    required this.title,
    required this.priority,
    this.dueDate,
    this.category,
    this.reminderAt,
  });
  final String title;
  final String priority;
  final String? dueDate;
  final String? category;

  /// Absolute reminder instant (`due − lead`), or null for no reminder.
  final String? reminderAt;
}

// ── Public helper ─────────────────────────────────────────────────────────────

/// Show the add-task sheet and return the submitted result, or null if
/// dismissed. Callers invoke the provider directly with the returned data.
///
/// Pass [initialDueDate] (e.g. from a calendar day-tap) to pre-select the due
/// date so the new task lands on that day. Omitting it preserves the original
/// behavior (no date pre-selected). [defaultLead] is the global reminder-lead
/// default applied once a due time is set without an explicit pick.
Future<
    ({
      String title,
      String priority,
      String? dueDate,
      String? category,
      String? reminderAt,
    })?> showAddTaskSheet(
  BuildContext context, {
  DateTime? initialDueDate,
  ReminderLead defaultLead = kDefaultReminderLead,
}) async {
  final result = await LzBottomSheet.show<_AddTaskResult>(
    context,
    title: 'New Task',
    builder: (_) => AddTaskSheet(
      initialDueDate: initialDueDate,
      defaultLead: defaultLead,
    ),
  );
  if (result == null) return null;
  return (
    title: result.title,
    priority: result.priority,
    dueDate: result.dueDate,
    category: result.category,
    reminderAt: result.reminderAt,
  );
}
