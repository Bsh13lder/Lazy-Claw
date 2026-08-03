/// The task detail sheet's REPEAT + ENDS block.
///
/// Extracted out of `task_detail_sheet.dart` for the same reason as
/// [TaskDueSection]: it was ~60 lines of markup inside one already-oversized
/// `build()`. Owns no state — it renders what it is handed and reports taps.
library;

import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/core/recurrence.dart';

import '../../ui/ui.dart';
import 'recurrence_picker.dart';
import 'task_section_label.dart';

class TaskRepeatSection extends StatelessWidget {
  const TaskRepeatSection({
    super.key,
    required this.recurrence,
    required this.recurUntil,
    required this.anchorWeekday,
    required this.onRecurrenceChanged,
    required this.onPickUntil,
    required this.onClearUntil,
  });

  final Recurrence recurrence;

  /// The series' end day (`yyyy-MM-dd`), or null = "Never".
  final String? recurUntil;

  /// Weekday the "weekly" option should anchor to (from the composed due
  /// date), or null when the task has no due date.
  final int? anchorWeekday;

  final ValueChanged<Recurrence> onRecurrenceChanged;
  final VoidCallback onPickUntil;
  final VoidCallback onClearUntil;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        TaskSectionLabel('REPEAT'),
        const SizedBox(height: AppSpacing.sm),
        RecurrencePicker(
          value: recurrence,
          anchorWeekday: anchorWeekday,
          onChanged: onRecurrenceChanged,
        ),

        // ── Series end (only when a recurrence is selected) ──────────────
        if (recurrence.repeats) ...[
          const SizedBox(height: AppSpacing.md),
          TaskSectionLabel('ENDS'),
          const SizedBox(height: AppSpacing.sm),
          // Wrap, not Row: two chips + a clear affordance overflow a narrow
          // phone once the end date is spelled out in the second chip.
          Wrap(
            spacing: AppSpacing.sm,
            runSpacing: AppSpacing.sm,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              LzChip(
                key: const Key('task-detail-recur-until-never'),
                label: 'Never',
                icon: Icons.all_inclusive,
                selected: recurUntil == null,
                color: AppColors.accent,
                onTap: onClearUntil,
              ),
              LzChip(
                key: const Key('task-detail-recur-until-date'),
                label: recurUntil ?? 'On date…',
                icon: Icons.event_outlined,
                selected: recurUntil != null,
                color: AppColors.info,
                onTap: onPickUntil,
              ),
              if (recurUntil != null)
                GestureDetector(
                  key: const Key('task-detail-recur-until-clear'),
                  onTap: onClearUntil,
                  child: const Icon(
                    Icons.close,
                    size: 16,
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
