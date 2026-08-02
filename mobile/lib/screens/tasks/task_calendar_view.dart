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
            // Roomier rows give the day number + its today/selected circle the
            // top of the cell and leave a clear band at the bottom for markers.
            rowHeight: 62,
            daysOfWeekHeight: 24,
            selectedDayPredicate: (day) => isSameDay(selectedDay, day),
            eventLoader: eventsFor,
            onDaySelected: onDaySelected,
            onPageChanged: onPageChanged,
            headerStyle: HeaderStyle(
              formatButtonVisible: false,
              titleCentered: true,
              headerPadding: const EdgeInsets.symmetric(
                vertical: AppSpacing.sm,
                horizontal: AppSpacing.xs,
              ),
              titleTextStyle: AppText.titleL,
              leftChevronIcon: const _CalChevron(Icons.chevron_left_rounded),
              rightChevronIcon: const _CalChevron(Icons.chevron_right_rounded),
              leftChevronPadding: EdgeInsets.zero,
              rightChevronPadding: EdgeInsets.zero,
              leftChevronMargin: const EdgeInsets.only(left: AppSpacing.xs),
              rightChevronMargin: const EdgeInsets.only(right: AppSpacing.xs),
            ),
            daysOfWeekStyle: DaysOfWeekStyle(
              weekdayStyle: AppText.caption.copyWith(
                color: AppColors.textMuted,
                fontWeight: FontWeight.w700,
              ),
              weekendStyle: AppText.caption.copyWith(
                color: AppColors.textMuted,
                fontWeight: FontWeight.w700,
              ),
            ),
            calendarStyle: CalendarStyle(
              defaultTextStyle: AppText.body,
              weekendTextStyle: AppText.body,
              outsideTextStyle:
                  AppText.body.copyWith(color: AppColors.textMuted),
              // Extra bottom inset shrinks + lifts the day-number circle so it
              // can't reach the marker band below it (the headline overlap fix).
              cellMargin: const EdgeInsets.fromLTRB(6, 6, 6, 14),
              // Don't hard-clip the dot band: on a narrow phone a 3-dot + "+N"
              // row can be a hair wider than the cell, and the 6px cellMargin
              // gutter absorbs the spill far more gracefully than a clipped "+N".
              canMarkersOverflow: true,
              markersAlignment: Alignment.bottomCenter,
              todayDecoration: BoxDecoration(
                color: AppColors.accent.withValues(alpha: 0.18),
                shape: BoxShape.circle,
                border: Border.all(color: AppColors.accent, width: 1),
              ),
              todayTextStyle: AppText.body.copyWith(
                color: AppColors.accent,
                fontWeight: FontWeight.w700,
              ),
              selectedDecoration: const BoxDecoration(
                gradient: AppColors.accentGradient,
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
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ),
            if (dayTasks.isNotEmpty) ...[
              _DaySummaryPill(dayTasks: dayTasks),
              const SizedBox(width: AppSpacing.sm),
            ],
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
              key: ValueKey('calendar-task-${dayTasks[i].id}'),
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

/// The bottom-of-cell marker for a day that has tasks.
///
/// Three visual states, all bottom-aligned so they never sit on the centered
/// day number:
///  * **Fully cleared** (`isDayAllDone`) → a single emerald check badge — the
///    whole day reads as "done" at a glance, distinct from any dot row.
///  * **Mixed / open** → a neatly spaced row of up to
///    [TaskCalendarView._maxDots] dots: open tasks lead as filled
///    project-colored dots, done tasks trail as hollow dimmed rings, and the
///    remainder collapses into a muted "+N". Each dot carries its own gap so
///    no dot ever overlaps another.
class _DayMarkers extends StatelessWidget {
  const _DayMarkers({required this.tasks, required this.colorByName});

  final List<Task> tasks;
  final Map<String, String> colorByName;

  /// Even gap between adjacent dots, as a fixed [SizedBox] so the row stays
  /// mathematically non-overlapping and evenly spaced at any cell width.
  static const double _dotGap = 4;

  /// Bottom inset that keeps the marker band off the cell's bottom edge while
  /// staying clear of the (lifted) day-number circle above it.
  static const double _bandBottom = 6;

  @override
  Widget build(BuildContext context) {
    // Everything due this day is finished → one clear "cleared" badge.
    if (isDayAllDone(tasks)) {
      return const Padding(
        padding: EdgeInsets.only(bottom: _bandBottom),
        child: _AllDoneBadge(),
      );
    }

    // Open work leads as filled dots; done work trails as hollow rings.
    final picked = pickDayMarkerTasks(tasks, maxDots: TaskCalendarView._maxDots);

    return Padding(
      padding: const EdgeInsets.only(bottom: _bandBottom),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          for (int i = 0; i < picked.shown.length; i++) ...[
            if (i > 0) const SizedBox(width: _dotGap),
            _TaskDot(
              color:
                  colorForTask(picked.shown[i], colorByName, AppColors.accent),
              done: picked.shown[i].isDone,
            ),
          ],
          if (picked.overflow > 0) ...[
            const SizedBox(width: _dotGap),
            Text(
              '+${picked.overflow}',
              style: AppText.caption.copyWith(
                color: AppColors.textMuted,
                fontSize: 9,
                height: 1,
                fontWeight: FontWeight.w700,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// A circular chevron button used in the month header (the "‹ June ›" affordance).
class _CalChevron extends StatelessWidget {
  const _CalChevron(this.icon);

  final IconData icon;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 34,
      height: 34,
      decoration: BoxDecoration(
        color: AppColors.bgSurfaceElevated,
        shape: BoxShape.circle,
        border: Border.all(color: AppColors.borderSubtle),
      ),
      child: Icon(icon, size: 22, color: AppColors.textSecondary),
    );
  }
}

/// A single day marker dot: a filled project-colored circle for an **open**
/// task, or a hollow dimmed ring for a **done** one.
class _TaskDot extends StatelessWidget {
  const _TaskDot({required this.color, required this.done});

  final Color color;
  final bool done;

  static const double _size = 6;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: _size,
      height: _size,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        // Open = solid fill; done = transparent with a faded ring.
        color: done ? null : color,
        border: done
            ? Border.all(color: color.withValues(alpha: 0.55), width: 1.2)
            : null,
      ),
    );
  }
}

/// A subtle emerald check badge shown when every task due on a day is done.
class _AllDoneBadge extends StatelessWidget {
  const _AllDoneBadge();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 13,
      height: 13,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: AppColors.success.withValues(alpha: 0.18),
        shape: BoxShape.circle,
      ),
      child: const Icon(
        Icons.check_rounded,
        size: 9,
        color: AppColors.success,
      ),
    );
  }
}

/// The selected-day status readout in the list header: "All clear" (emerald)
/// when the day is fully done, otherwise an open/done breakdown.
class _DaySummaryPill extends StatelessWidget {
  const _DaySummaryPill({required this.dayTasks});

  final List<Task> dayTasks;

  @override
  Widget build(BuildContext context) {
    if (isDayAllDone(dayTasks)) {
      return const LzPill(
        label: 'All clear',
        icon: Icons.check_circle_outline,
        color: AppColors.success,
      );
    }
    final counts = dayTaskCounts(dayTasks);
    final label = counts.done > 0
        ? '${counts.open} open · ${counts.done} done'
        : '${counts.open} open';
    return LzPill(label: label, dotColor: AppColors.accent);
  }
}
