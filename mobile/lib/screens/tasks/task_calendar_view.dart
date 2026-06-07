import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:table_calendar/table_calendar.dart';

import '../../models/project.dart';
import '../../models/task.dart';
import 'task_calendar_utils.dart';
import 'task_row.dart';

/// The Tasks-tab calendar body: a month [TableCalendar] with per-project-colored
/// event dots keyed by due date, plus the selected day's tasks rendered as
/// [TaskRow]s underneath.
///
/// Purely presentational — all state (focused / selected day) and the mutating
/// callbacks live in the parent [TasksScreen] so the FAB can add to the
/// currently selected day.
class TaskCalendarView extends StatelessWidget {
  const TaskCalendarView({
    super.key,
    required this.tasks,
    required this.projects,
    required this.dirtyIds,
    required this.focusedDay,
    required this.selectedDay,
    required this.onDaySelected,
    required this.onPageChanged,
    required this.onComplete,
    required this.onDelete,
    required this.onOpen,
    required this.onAddOnDay,
  });

  final List<Task> tasks;
  final List<Project> projects;
  final Set<String> dirtyIds;
  final DateTime focusedDay;
  final DateTime? selectedDay;
  final void Function(DateTime selected, DateTime focused) onDaySelected;
  final void Function(DateTime focused) onPageChanged;
  final void Function(String id) onComplete;
  final void Function(String id) onDelete;
  final void Function(Task task) onOpen;
  final void Function(DateTime day) onAddOnDay;

  /// How many colored dots to render under a day before collapsing to "+N".
  static const int _maxDots = 3;

  @override
  Widget build(BuildContext context) {
    final grouped = groupTasksByDay(tasks);
    final colorByName = projectColorMap(projects);

    List<Task> eventsFor(DateTime day) =>
        grouped[DateTime(day.year, day.month, day.day)] ?? const [];

    final now = DateTime.now();
    final selected = selectedDay ?? DateTime(now.year, now.month, now.day);
    final dayTasks = eventsFor(selected);

    return ListView(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.lg,
        AppSpacing.md,
        AppSpacing.lg,
        AppSpacing.xxxl, // leave room above the FAB
      ),
      children: [
        // ── Month calendar ─────────────────────────────────────────────────
        LzCard(
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.sm,
            vertical: AppSpacing.sm,
          ),
          child: TableCalendar<Task>(
            firstDay: DateTime(now.year - 2, 1, 1),
            lastDay: DateTime(now.year + 5, 12, 31),
            focusedDay: focusedDay,
            currentDay: DateTime(now.year, now.month, now.day),
            calendarFormat: CalendarFormat.month,
            availableGestures: AvailableGestures.horizontalSwipe,
            startingDayOfWeek: StartingDayOfWeek.monday,
            selectedDayPredicate: (day) => isSameDay(selectedDay, day),
            eventLoader: eventsFor,
            onDaySelected: onDaySelected,
            onPageChanged: onPageChanged,
            headerStyle: HeaderStyle(
              formatButtonVisible: false,
              titleCentered: true,
              titleTextStyle: AppText.title,
              leftChevronIcon: const Icon(Icons.chevron_left,
                  color: AppColors.textSecondary),
              rightChevronIcon: const Icon(Icons.chevron_right,
                  color: AppColors.textSecondary),
            ),
            daysOfWeekStyle: DaysOfWeekStyle(
              weekdayStyle:
                  AppText.caption.copyWith(color: AppColors.textMuted),
              weekendStyle:
                  AppText.caption.copyWith(color: AppColors.textMuted),
            ),
            calendarStyle: CalendarStyle(
              defaultTextStyle: AppText.body,
              weekendTextStyle: AppText.body,
              outsideTextStyle:
                  AppText.body.copyWith(color: AppColors.textMuted),
              todayDecoration: BoxDecoration(
                color: AppColors.accent.withValues(alpha: 0.18),
                shape: BoxShape.circle,
                border: Border.all(color: AppColors.accent, width: 1),
              ),
              todayTextStyle: AppText.body.copyWith(color: AppColors.accent),
              selectedDecoration: const BoxDecoration(
                color: AppColors.accent,
                shape: BoxShape.circle,
              ),
              selectedTextStyle: AppText.body.copyWith(
                color: AppColors.onAccent,
                fontWeight: FontWeight.w700,
              ),
              // Custom marker builder draws its own dots; disable the default.
              markersMaxCount: 0,
            ),
            calendarBuilders: CalendarBuilders<Task>(
              markerBuilder: (context, day, events) {
                if (events.isEmpty) return null;
                return _DayMarkers(
                  tasks: events,
                  colorByName: colorByName,
                );
              },
            ),
          ),
        ),

        const SizedBox(height: AppSpacing.xl),

        // ── Selected day header + add affordance ───────────────────────────
        Row(
          children: [
            Expanded(
              child: Text(
                _formatDayHeader(selected),
                style: AppText.title,
              ),
            ),
            if (dayTasks.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(right: AppSpacing.sm),
                child: Text(
                  dayTasks.length.toString(),
                  style: AppText.caption.copyWith(color: AppColors.textMuted),
                ),
              ),
            LzIconButton(
              icon: Icons.add,
              tooltip: 'Add task on this day',
              onPressed: () => onAddOnDay(selected),
            ),
          ],
        ),

        const SizedBox(height: AppSpacing.md),

        // ── Selected day's tasks ───────────────────────────────────────────
        if (dayTasks.isEmpty)
          LzEmptyState(
            icon: Icons.event_available_outlined,
            title: 'Nothing due this day',
            hint: 'Tap + to add a task on ${_formatDayShort(selected)}.',
          )
        else
          for (int i = 0; i < dayTasks.length; i++) ...[
            TaskRow(
              task: dayTasks[i],
              pendingSync: dirtyIds.contains(dayTasks[i].id),
              onComplete: () => onComplete(dayTasks[i].id),
              onDelete: () => onDelete(dayTasks[i].id),
              onTap: () => onOpen(dayTasks[i]),
            ),
            if (i < dayTasks.length - 1)
              const SizedBox(height: AppSpacing.sm),
          ],
      ],
    );
  }

  static const _months = [
    'January', 'February', 'March', 'April', 'May', 'June', 'July',
    'August', 'September', 'October', 'November', 'December',
  ];
  static const _weekdays = [
    'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday',
    'Sunday',
  ];

  String _formatDayHeader(DateTime d) {
    // DateTime.weekday is 1 (Mon) .. 7 (Sun).
    final wd = _weekdays[d.weekday - 1];
    final mo = _months[d.month - 1];
    return '$wd, $mo ${d.day}';
  }

  String _formatDayShort(DateTime d) => '${_months[d.month - 1]} ${d.day}';
}

/// A compact row of up to [TaskCalendarView._maxDots] colored dots under a day
/// cell, one per task colored by its project, collapsing the remainder into a
/// muted "+N" label.
class _DayMarkers extends StatelessWidget {
  const _DayMarkers({required this.tasks, required this.colorByName});

  final List<Task> tasks;
  final Map<String, String> colorByName;

  @override
  Widget build(BuildContext context) {
    final shown = tasks.take(TaskCalendarView._maxDots).toList();
    final overflow = tasks.length - shown.length;

    return Padding(
      padding: const EdgeInsets.only(bottom: 5),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          for (final t in shown)
            Container(
              width: 6,
              height: 6,
              margin: const EdgeInsets.symmetric(horizontal: 1),
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: colorForTask(t, colorByName, AppColors.accent),
              ),
            ),
          if (overflow > 0)
            Padding(
              padding: const EdgeInsets.only(left: 2),
              child: Text(
                '+$overflow',
                style: AppText.caption.copyWith(
                  color: AppColors.textMuted,
                  fontSize: 8,
                  height: 1,
                ),
              ),
            ),
        ],
      ),
    );
  }
}
