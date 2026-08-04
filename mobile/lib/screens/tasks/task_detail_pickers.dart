/// Themed date/time pickers and the ISO day helpers the task detail sheet
/// drives them with.
///
/// Extracted out of `task_detail_sheet.dart` — three near-identical
/// `showDatePicker`/`showTimePicker` calls, each re-declaring the same dark
/// `Theme` wrapper, were ~90 lines of boilerplate in a file already past this
/// project's 800-line ceiling. The SEEDS are pure functions so the
/// "which day does the picker open on" rules are unit-testable without a
/// widget tree.
library;

import 'package:flutter/material.dart';

import '../../ui/ui.dart';

/// A `yyyy-MM-dd` string for [d]. Pure; zero-padded so it sorts lexically.
String isoDay(DateTime d) =>
    '${d.year.toString().padLeft(4, '0')}-'
    '${d.month.toString().padLeft(2, '0')}-'
    '${d.day.toString().padLeft(2, '0')}';

/// The day a DUE-DATE picker should open on, given the task's current
/// [currentDay] (a `yyyy-MM-dd` string, or null).
///
/// Defaults to tomorrow. A stored day in the PAST is deliberately ignored:
/// re-opening the picker on an overdue date makes "pick a new date" start from
/// the wrong month. An unparseable value falls back to the default rather than
/// throwing. [now] is injectable so the rule is testable without waiting for
/// midnight.
DateTime dueDayPickerSeed(String? currentDay, {DateTime? now}) {
  final today = now ?? DateTime.now();
  final fallback = today.add(const Duration(days: 1));
  if (currentDay == null) return fallback;
  final parsed = DateTime.tryParse(currentDay);
  if (parsed == null) return fallback;
  if (parsed.isBefore(DateTime(today.year, today.month, today.day))) {
    return fallback;
  }
  return parsed;
}

/// The day a series-END picker should open on. Defaults to 30 days out; an
/// existing end date in the future wins. Past/unparseable values fall back.
DateTime recurUntilPickerSeed(String? currentDay, {DateTime? now}) {
  final today = now ?? DateTime.now();
  final fallback = today.add(const Duration(days: 30));
  if (currentDay == null) return fallback;
  final parsed = DateTime.tryParse(currentDay);
  if (parsed == null || parsed.isBefore(today)) return fallback;
  return parsed;
}

/// The dark colour scheme every picker in this sheet wears. One definition so
/// the three call sites can't drift apart.
Widget _themed(BuildContext ctx, Widget? child) => Theme(
  data: Theme.of(ctx).copyWith(
    colorScheme: const ColorScheme.dark(
      primary: AppColors.accent,
      surface: AppColors.bgSurfaceElevated,
    ),
  ),
  child: child!,
);

/// A themed [showDatePicker]. Returns null when dismissed.
Future<DateTime?> showTaskDatePicker(
  BuildContext context, {
  required DateTime initialDate,
  required DateTime firstDate,
  required DateTime lastDate,
}) => showDatePicker(
  context: context,
  initialDate: initialDate,
  firstDate: firstDate,
  lastDate: lastDate,
  builder: _themed,
);

/// A themed [showTimePicker], defaulting to 9:00. Returns null when dismissed.
Future<TimeOfDay?> showTaskTimePicker(
  BuildContext context, {
  TimeOfDay? initial,
}) => showTimePicker(
  context: context,
  initialTime: initial ?? const TimeOfDay(hour: 9, minute: 0),
  builder: _themed,
);

// ── Seeded pickers ──────────────────────────────────────────────────────────
//
// The detail sheet used to hold these three as `State` methods that each did
// the same three things: compute the seed, compute the range, `setState` the
// result. Only the middle step was ever sheet-specific, and it isn't really —
// so the whole "which window does this picker open on" decision lives here
// with the seeds it depends on, and the sheet is left with the assignment.
// (It also bought back the lines auto-save needed; that file is on the
// 800-line ceiling.)

/// Pick a DUE day. Returns the chosen `yyyy-MM-dd`, or null when dismissed.
///
/// The ±365-day window is deliberately symmetric: a task can be back-dated
/// (logging something already missed) as easily as scheduled.
Future<String?> pickTaskDueDay(
  BuildContext context, {
  required String? currentDay,
}) async {
  final now = DateTime.now();
  final picked = await showTaskDatePicker(
    context,
    initialDate: dueDayPickerSeed(currentDay, now: now),
    firstDate: now.subtract(const Duration(days: 365)),
    lastDate: now.add(const Duration(days: 365)),
  );
  return picked == null ? null : isoDay(picked);
}

/// Pick the recurring series' END day. Returns `yyyy-MM-dd`, or null when
/// dismissed.
///
/// A long horizon (10 years) so a yearly recurrence can still be given a
/// meaningful end date, and no past dates — an already-elapsed end would
/// retire the series on the spot.
Future<String?> pickTaskRecurUntilDay(
  BuildContext context, {
  required String? currentDay,
}) async {
  final now = DateTime.now();
  final picked = await showTaskDatePicker(
    context,
    initialDate: recurUntilPickerSeed(currentDay, now: now),
    firstDate: now,
    lastDate: now.add(const Duration(days: 3650)),
  );
  return picked == null ? null : isoDay(picked);
}

/// Today as `yyyy-MM-dd`.
String isoToday() => isoDay(DateTime.now());

/// Tomorrow as `yyyy-MM-dd`.
String isoTomorrow() => isoDay(DateTime.now().add(const Duration(days: 1)));
