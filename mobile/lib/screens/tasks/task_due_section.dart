/// The task detail sheet's DUE DATE + REMIND block.
///
/// Extracted out of `task_detail_sheet.dart` — this was ~165 lines of markup
/// inside one `build()` in a file already past the 800-line ceiling.
///
/// Deliberately dumb: it owns NO state and derives nothing. Every value it
/// renders (including the two composed/derived strings) is computed by the
/// sheet and handed down, and every interaction is reported straight back up.
/// That matters here more than usual — the reminder-preservation rules behind
/// [survivingReminderAt] are the most bug-prone logic in this sheet (see the
/// `_reminderArg` doc and `detail_sheet_reminder_preserve_test.dart`), and
/// splitting them across two files is how they'd drift.
library;

import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/core/due_date.dart';
import 'package:lazyclaw_mobile/core/reminder_lead.dart';

import '../../ui/ui.dart';
import 'reminder_lead_picker.dart';
import 'task_section_label.dart';

class TaskDueSection extends StatelessWidget {
  const TaskDueSection({
    super.key,
    required this.dueDay,
    required this.dueTime,
    required this.composedDue,
    required this.survivingReminderAt,
    required this.effectiveLead,
    required this.todayIso,
    required this.tomorrowIso,
    required this.onReschedule,
    required this.onToggleDay,
    required this.onPickDay,
    required this.onPickTime,
    required this.onClearTime,
    required this.onClearDue,
    required this.onLeadChanged,
    required this.onClearReminder,
  });

  /// The selected calendar day (`yyyy-MM-dd`), or null for none.
  final String? dueDay;
  final TimeOfDay? dueTime;

  /// [dueDay] + [dueTime] as the sheet will persist them, or null.
  final String? composedDue;

  /// The reminder instant that survives this edit, or null when nothing does.
  final String? survivingReminderAt;

  final ReminderLead effectiveLead;

  /// Pre-computed so this widget calls no clock of its own — a chip that
  /// decided "is this today?" against a different `DateTime.now()` than the
  /// parent's would flicker on a midnight boundary.
  final String todayIso;
  final String tomorrowIso;

  final VoidCallback onReschedule;

  /// Tapping Today/Tomorrow. The parent decides whether that means select or
  /// deselect (both chips are toggles).
  final ValueChanged<String> onToggleDay;

  final VoidCallback onPickDay;
  final VoidCallback onPickTime;
  final VoidCallback onClearTime;
  final VoidCallback onClearDue;
  final ValueChanged<ReminderLead> onLeadChanged;
  final VoidCallback onClearReminder;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        Row(
          children: [
            TaskSectionLabel('DUE DATE'),
            const Spacer(),
            LzButton.ghost(
              key: const Key('task-detail-reschedule'),
              label: 'Reschedule',
              icon: Icons.event_repeat_outlined,
              onPressed: onReschedule,
            ),
          ],
        ),
        const SizedBox(height: AppSpacing.sm),
        // Wrap, not Row: three chips overflow a narrow phone.
        Wrap(
          spacing: AppSpacing.sm,
          runSpacing: AppSpacing.sm,
          children: [
            LzChip(
              label: 'Today',
              icon: Icons.today_outlined,
              selected: dueDay == todayIso,
              color: AppColors.warn,
              onTap: () => onToggleDay(todayIso),
            ),
            LzChip(
              label: 'Tomorrow',
              icon: Icons.event_outlined,
              selected: dueDay == tomorrowIso,
              color: AppColors.accent,
              onTap: () => onToggleDay(tomorrowIso),
            ),
            LzChip(
              label: 'Pick…',
              icon: Icons.calendar_month_outlined,
              selected:
                  dueDay != null && dueDay != todayIso && dueDay != tomorrowIso,
              color: AppColors.info,
              onTap: onPickDay,
            ),
          ],
        ),

        // ── Time-of-day chip ────────────────────────────────────────────
        const SizedBox(height: AppSpacing.sm),
        Row(
          children: [
            LzChip(
              label: dueTime != null
                  ? formatClock12(dueTime!.hour, dueTime!.minute)
                  : 'Add time',
              icon: Icons.schedule_outlined,
              selected: dueTime != null,
              color: AppColors.accent,
              onTap: onPickTime,
            ),
            if (dueTime != null) ...[
              const SizedBox(width: AppSpacing.sm),
              GestureDetector(
                onTap: onClearTime,
                child: const Icon(
                  Icons.close,
                  size: 16,
                  color: AppColors.textMuted,
                ),
              ),
            ],
          ],
        ),

        if (composedDue != null) ...[
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              const Icon(
                Icons.event_available_outlined,
                size: 14,
                color: AppColors.textMuted,
              ),
              const SizedBox(width: AppSpacing.xs),
              Text(
                'Due ${dueDateDisplay(composedDue!)}',
                style: AppText.caption.copyWith(color: AppColors.textSecondary),
              ),
              const Spacer(),
              GestureDetector(
                key: const Key('task-detail-due-clear'),
                onTap: onClearDue,
                child: const Icon(
                  Icons.close,
                  size: 14,
                  color: AppColors.textMuted,
                ),
              ),
            ],
          ),
        ],

        // ── Reminder lead-time (only when a due TIME exists) ─────────────
        if (dueDateHasTime(composedDue)) ...[
          const SizedBox(height: AppSpacing.xl),
          TaskSectionLabel('REMIND'),
          const SizedBox(height: AppSpacing.sm),
          ReminderLeadPicker(value: effectiveLead, onChanged: onLeadChanged),
        ]
        // A DATE-ONLY due can't express a lead (`due − lead` degenerates), but
        // the task may still carry a real timed reminder — every respawned
        // recurring occurrence does. Show it read-only with a ✕, so the user
        // can SEE the reminder instead of the sheet silently implying "None".
        else if (survivingReminderAt != null) ...[
          const SizedBox(height: AppSpacing.xl),
          TaskSectionLabel('REMIND'),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              const Icon(
                Icons.notifications_active_outlined,
                size: 14,
                color: AppColors.textMuted,
              ),
              const SizedBox(width: AppSpacing.xs),
              Text(
                dueDateDisplay(survivingReminderAt!),
                key: const Key('task-detail-reminder'),
                style: AppText.caption.copyWith(color: AppColors.textSecondary),
              ),
              const Spacer(),
              GestureDetector(
                key: const Key('task-detail-reminder-clear'),
                onTap: onClearReminder,
                child: const Icon(
                  Icons.close,
                  size: 14,
                  color: AppColors.textMuted,
                ),
              ),
            ],
          ),
        ],
      ],
    );
  }
}
