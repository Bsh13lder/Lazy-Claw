import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/core/due_date.dart';
import 'package:lazyclaw_mobile/core/reschedule_dates.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../models/task.dart';
import '../../providers/tasks_provider.dart';
import 'chip_edit.dart';

/// Open the Smart Fast Reschedule sheet for a single [task].
///
/// A single tap on a quick-date chip (Tomorrow · This weekend · Next week)
/// lands the task on that day at the chosen time (default **10:00 AM**) and
/// closes the sheet. "Pick a date…" opens the themed calendar, then applies at
/// the same time. The time row lets the user override the 10 AM default first.
///
/// Writes route through [TasksNotifier.updateTask] — optimistic cache + outbox
/// + best-effort sync + local-notification reschedule — so this works offline.
Future<void> showRescheduleSheet(
  BuildContext context,
  WidgetRef ref,
  Task task,
) {
  return _showSheet(
    context,
    taskIds: [task.id],
    heading: 'Reschedule',
  );
}

/// Open the reschedule sheet for a *set* of overdue [tasks] ("Reschedule all").
///
/// Identical UX to the single-task sheet — pick a date once (default 10:00 AM,
/// overridable time) — but applies to every task id at once via
/// [TasksNotifier.rescheduleMany] (one batched cache refresh + one sync). The
/// heading reflects the count ("Reschedule 5 overdue").
Future<void> showRescheduleAllSheet(
  BuildContext context,
  WidgetRef ref,
  List<Task> tasks,
) {
  final ids = tasks.map((t) => t.id).toList(growable: false);
  return _showSheet(
    context,
    taskIds: ids,
    heading: 'Reschedule ${ids.length} overdue',
  );
}

/// Shared launcher for both the single- and multi-task reschedule flows.
Future<void> _showSheet(
  BuildContext context, {
  required List<String> taskIds,
  required String heading,
}) {
  return LzBottomSheet.show<void>(
    context,
    title: heading,
    builder: (_) => _RescheduleSheet(taskIds: taskIds),
  );
}

class _RescheduleSheet extends ConsumerStatefulWidget {
  const _RescheduleSheet({required this.taskIds});

  /// The task ids this sheet reschedules. One id → a single-task update; many
  /// ids → a batched [TasksNotifier.rescheduleMany].
  final List<String> taskIds;

  @override
  ConsumerState<_RescheduleSheet> createState() => _RescheduleSheetState();
}

class _RescheduleSheetState extends ConsumerState<_RescheduleSheet> {
  /// The time-of-day the picked day lands at. Defaults to 10:00 AM per the
  /// Smart-Reschedule "boom" rule; the user can override it before tapping a
  /// date via the time row.
  TimeOfDay _time = const TimeOfDay(hour: 10, minute: 0);

  /// Apply [day] at the currently-selected time to every task id, then close
  /// the sheet. The `dueDate` lands date-only (keeps backend overdue/bucket
  /// queries correct) and `reminderAt` carries the time.
  Future<void> _applyDay(DateTime day) async {
    final target = composeAt(day, hour: _time.hour, minute: _time.minute);
    HapticFeedback.selectionClick();
    final notifier = ref.read(tasksProvider.notifier);
    final ids = widget.taskIds;
    if (ids.length == 1) {
      await notifier.updateTask(
        ids.first,
        dueDate: target.dueDate,
        reminderAt: target.reminderAt,
      );
    } else {
      await notifier.rescheduleMany(
        ids,
        dueDate: target.dueDate,
        reminderAt: target.reminderAt,
      );
    }
    if (!mounted) return;
    Navigator.of(context).pop();
  }

  Future<void> _pickTime() async {
    final picked = await showThemedTimePicker(context, initial: _time);
    if (picked == null || !mounted) return;
    setState(() => _time = picked);
  }

  Future<void> _pickDate() async {
    final picked = await showThemedDatePicker(context);
    if (picked == null || !mounted) return;
    await _applyDay(DateTime(picked.year, picked.month, picked.day));
  }

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    final timeLabel = formatClock12(_time.hour, _time.minute);

    return SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // ── Time row: "Time: 10:00 AM · change" ───────────────────────────
          LzListTile(
            key: const Key('reschedule-time-row'),
            leading: Icon(
              Icons.schedule_outlined,
              size: 18,
              color: AppColors.accent,
            ),
            title: 'Time: $timeLabel',
            subtitle: 'Tap to change',
            trailing: Icon(
              Icons.edit_outlined,
              size: 16,
              color: AppColors.textMuted,
            ),
            onTap: _pickTime,
          ),

          const SizedBox(height: AppSpacing.lg),

          // ── Quick-date chips ──────────────────────────────────────────────
          Wrap(
            spacing: AppSpacing.sm,
            runSpacing: AppSpacing.sm,
            children: [
              LzChip(
                key: const Key('reschedule-tomorrow'),
                label: 'Tomorrow',
                icon: Icons.wb_sunny_outlined,
                color: AppColors.accent,
                onTap: () => _applyDay(tomorrow(now)),
              ),
              LzChip(
                key: const Key('reschedule-this-weekend'),
                label: 'This weekend',
                icon: Icons.weekend_outlined,
                color: AppColors.info,
                onTap: () => _applyDay(thisWeekend(now)),
              ),
              LzChip(
                key: const Key('reschedule-next-week'),
                label: 'Next week',
                icon: Icons.event_outlined,
                color: AppColors.info,
                onTap: () => _applyDay(nextWeek(now)),
              ),
              LzChip(
                key: const Key('reschedule-pick-date'),
                label: 'Pick a date…',
                icon: Icons.calendar_month_outlined,
                color: AppColors.textMuted,
                onTap: _pickDate,
              ),
            ],
          ),
        ],
      ),
    );
  }
}
