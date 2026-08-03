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
    this.ghostsNow,
    this.showRepeats = true,
    this.onShowRepeatsChanged,
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

  /// Injected "now" for the recurrence-ghost projection
  /// ([expandRecurringForRange]'s `now` — the day before which no ghost is
  /// ever generated). Null (the production default) falls through to the
  /// real wall clock. Deliberately separate from the `DateTime.now()` used
  /// below for today-highlighting/date bounds: a test can pin what "today"
  /// means for the ghost clamp without also faking which day the calendar
  /// considers "today" for its own chrome (the today circle, first/last
  /// day bounds). Without this, a widget test asserting a ghost renders on
  /// a hardcoded future day would silently start failing once the real
  /// wall clock caught up to that day.
  final DateTime? ghostsNow;

  /// Master "Show repeats" toggle for the recurrence-ghost projection below
  /// (persisted one level up in `TasksScreen` via `UiPrefsDao` /
  /// `kPrefCalendarShowRepeats`). Defaults to true, matching the behavior
  /// before this toggle existed. When false, [expandRecurringForRange] is
  /// not even called — the work is skipped, not just its rendering — so the
  /// selected-day list shows no ghost rows either.
  final bool showRepeats;

  /// Fired with the new value when the "Show repeats" toggle is tapped. Null
  /// hides the toggle affordance entirely — mirrors
  /// `TasksProjectView.onHideCompletedChanged`.
  final ValueChanged<bool>? onShowRepeatsChanged;

  /// How many colored dots to render under a day before collapsing to "+N".
  static const int _maxDots = 3;

  @override
  Widget build(BuildContext context) {
    final grouped = groupTasksByDay(tasks);
    final colorByName = projectColorMap(projects);

    // Recurring tasks are materialised ONE occurrence at a time server-side
    // (`tasks/store.py` respawns the next occurrence only on completion) —
    // nothing else expands `recurring` for display, so a daily/weekly task
    // would otherwise occupy a single calendar cell. Project the visible
    // range (generously padded a month either side of the focused month, so
    // table_calendar's leading/trailing grid days stay covered too) into
    // GHOST entries and merge them in alongside the real day map — unless
    // the user has turned ghosts off, in which case skip the projection
    // entirely rather than computing it and discarding the result.
    final rangeStart = DateTime(focusedDay.year, focusedDay.month - 1, 1);
    final rangeEnd = DateTime(focusedDay.year, focusedDay.month + 2, 1)
        .subtract(const Duration(days: 1));
    final ghostGrouped = showRepeats
        ? expandRecurringForRange(tasks, rangeStart, rangeEnd, now: ghostsNow)
        : const <DateTime, List<Task>>{};

    List<Task> eventsFor(DateTime day) =>
        grouped[DateTime(day.year, day.month, day.day)] ?? const [];
    List<Task> ghostsFor(DateTime day) =>
        ghostGrouped[DateTime(day.year, day.month, day.day)] ?? const [];

    final now = DateTime.now();
    final selected = selectedDay ?? DateTime(now.year, now.month, now.day);
    final dayTasks = eventsFor(selected);
    final dayGhosts = ghostsFor(selected);
    final repeatsToggle = _repeatsToggle();

    return ListView(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.lg,
        AppSpacing.md,
        AppSpacing.lg,
        AppSpacing.xxxl, // leave room above the FAB
      ),
      children: [
        // ── "Show repeats" toggle ────────────────────────────────────────────
        if (repeatsToggle != null) ...[
          Align(alignment: Alignment.centerRight, child: repeatsToggle),
          const SizedBox(height: AppSpacing.sm),
        ],

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
                final ghosts = ghostsFor(day);
                if (events.isEmpty && ghosts.isEmpty) return null;
                return _DayMarkers(
                  tasks: events,
                  ghosts: ghosts,
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
        if (dayTasks.isEmpty && dayGhosts.isEmpty)
          LzEmptyState(
            icon: Icons.event_available_outlined,
            title: 'Nothing due this day',
            hint: 'Tap + to add a task on ${_formatDayShort(selected)}.',
          )
        else ...[
          for (int i = 0; i < dayTasks.length; i++) ...[
            TaskRow(
              key: ValueKey('calendar-task-${dayTasks[i].id}'),
              task: dayTasks[i],
              pendingSync: dirtyIds.contains(dayTasks[i].id),
              onComplete: () => onComplete(dayTasks[i].id),
              onDelete: () => onDelete(dayTasks[i].id),
              onTap: () => onOpen(dayTasks[i]),
            ),
            if (i < dayTasks.length - 1 || dayGhosts.isNotEmpty)
              const SizedBox(height: AppSpacing.sm),
          ],
          // Ghosts: display-only projections of an upcoming repeat, never a
          // real materialised row — no complete/delete affordance, so a tap
          // can never act on the wrong day/occurrence.
          for (int i = 0; i < dayGhosts.length; i++) ...[
            _GhostRow(
              key: ValueKey('calendar-ghost-${dayGhosts[i].id}'),
              task: dayGhosts[i],
            ),
            if (i < dayGhosts.length - 1)
              const SizedBox(height: AppSpacing.sm),
          ],
        ],
      ],
    );
  }

  /// The recurrence-ghost "Show repeats" toggle — mirrors
  /// `TasksProjectView`'s hide-completed eye toggle: a small muted icon
  /// button that swaps glyph on the current state and reports the flipped
  /// value to the caller, which owns persistence (`UiPrefsDao` /
  /// `kPrefCalendarShowRepeats`). Returns null (hides the affordance
  /// entirely) when [onShowRepeatsChanged] is null.
  Widget? _repeatsToggle() {
    final onChanged = onShowRepeatsChanged;
    if (onChanged == null) return null;
    return GestureDetector(
      key: const ValueKey('calendar-show-repeats-toggle'),
      behavior: HitTestBehavior.opaque,
      onTap: () => onChanged(!showRepeats),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            showRepeats ? Icons.repeat_on_rounded : Icons.repeat_rounded,
            size: 18,
            color: AppColors.textMuted,
          ),
          const SizedBox(width: AppSpacing.xs),
          Text(
            'Show repeats',
            style: AppText.caption.copyWith(color: AppColors.textMuted),
          ),
        ],
      ),
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

/// The bottom-of-cell marker for a day that has tasks and/or recurring-task
/// ghosts (a projected, not-yet-materialised repeat — see
/// [expandRecurringForRange]).
///
/// Visual states, all bottom-aligned so they never sit on the centered day
/// number:
///  * **Fully cleared, no ghosts** (`isDayAllDone` + no ghosts) → a single
///    solid emerald dot, the same visual weight as a real [_TaskDot] — the
///    whole day reads as "done" at a glance instead of blank space.
///  * **Everything else** → a neatly spaced row of up to
///    [TaskCalendarView._maxDots] dots: open tasks lead as filled
///    project-colored dots, done tasks trail as hollow dimmed rings, and (only
///    when a slot remains) AT MOST ONE ghost trails last as a dimmer hollow
///    ring (visually distinct from a "done" ring — never counted toward
///    done/undone math, and never inflating the overflow count below — see
///    [pickDayMarkers]). The remainder of the REAL tasks collapses into a
///    muted "+N"; ghosts never contribute to that number. Each dot carries
///    its own gap so no dot ever overlaps another.
class _DayMarkers extends StatelessWidget {
  const _DayMarkers({
    required this.tasks,
    required this.ghosts,
    required this.colorByName,
  });

  final List<Task> tasks;

  /// Recurring tasks' projected (not real) occurrences on this day. Never
  /// participates in [isDayAllDone] / done math — purely a "there's an
  /// upcoming repeat here" hint.
  final List<Task> ghosts;

  final Map<String, String> colorByName;

  /// Even gap between adjacent dots, as a fixed [SizedBox] so the row stays
  /// mathematically non-overlapping and evenly spaced at any cell width.
  static const double _dotGap = 4;

  /// Bottom inset that keeps the marker band off the cell's bottom edge while
  /// staying clear of the (lifted) day-number circle above it.
  static const double _bandBottom = 6;

  @override
  Widget build(BuildContext context) {
    // Everything real due this day is finished AND there's no ghost to also
    // show → one clear "cleared" badge.
    if (isDayAllDone(tasks) && ghosts.isEmpty) {
      return const Padding(
        padding: EdgeInsets.only(bottom: _bandBottom),
        child: _AllDoneBadge(),
      );
    }

    // Open work leads as filled dots; done work trails as hollow rings. At
    // most one ghost occupies a remaining slot (never inflating overflow —
    // see pickDayMarkers) — ghosts are a speculative hint, not real work, so
    // a day full of them must never look identical to every other day.
    final picked =
        pickDayMarkers(tasks, ghosts, maxDots: TaskCalendarView._maxDots);

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
          if (picked.ghost != null) ...[
            if (picked.shown.isNotEmpty) const SizedBox(width: _dotGap),
            _TaskDot(
              key: ValueKey('ghost-marker-${picked.ghost!.id}'),
              color: colorForTask(picked.ghost!, colorByName, AppColors.accent),
              done: false,
              ghost: true,
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
/// task, a hollow dimmed ring for a **done** one, or a dimmer hollow ring for
/// a [ghost] (a recurring task's projected, not-yet-materialised occurrence —
/// see [expandRecurringForRange]). [ghost] always renders hollow regardless
/// of [done] — a projection is never "done" or "open", it's a hint that a
/// repeat is coming — and uses a lower ring alpha than a real done task so
/// the two are still visually distinguishable from each other.
class _TaskDot extends StatelessWidget {
  const _TaskDot({
    super.key,
    required this.color,
    required this.done,
    this.ghost = false,
  });

  final Color color;
  final bool done;
  final bool ghost;

  static const double _size = 6;

  @override
  Widget build(BuildContext context) {
    final hollow = done || ghost;
    return Container(
      width: _size,
      height: _size,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        // Open = solid fill; done/ghost = transparent with a faded ring.
        color: hollow ? null : color,
        border: hollow
            ? Border.all(
                color: color.withValues(alpha: ghost ? 0.4 : 0.55),
                width: 1.2,
              )
            : null,
      ),
    );
  }
}

/// A solid marker shown when every task due on a day is done — the same
/// visual weight as a real [_TaskDot] (filled `AppColors.success` circle) so
/// a fully-cleared day still reads as a real marker instead of the near-
/// invisible 18%-alpha ring it used to be (diagnosis D3).
class _AllDoneBadge extends StatelessWidget {
  const _AllDoneBadge();

  @override
  Widget build(BuildContext context) {
    return const _TaskDot(color: AppColors.success, done: false);
  }
}

/// A selected-day-list row for a recurring task's projected GHOST occurrence
/// — display-only, dimmed, and carrying NO complete/delete affordance (the
/// real materialised row is the only thing allowed to act on the task's id).
class _GhostRow extends StatelessWidget {
  const _GhostRow({super.key, required this.task});

  final Task task;

  @override
  Widget build(BuildContext context) {
    return LzCard(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.sm,
      ),
      child: Row(
        children: [
          const Icon(
            Icons.repeat_rounded,
            size: 16,
            color: AppColors.textMuted,
          ),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Text(
              task.title,
              style: AppText.body.copyWith(color: AppColors.textMuted),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          Text(
            'Repeats',
            style: AppText.caption.copyWith(color: AppColors.textMuted),
          ),
        ],
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
