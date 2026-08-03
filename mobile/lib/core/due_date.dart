/// Helpers for the dual-shape `dueDate` string used throughout the Tasks UI.
///
/// A task's due date is stored as a nullable ISO string in two shapes:
///   * **date-only** — `yyyy-MM-dd` (length 10, no time), e.g. `2026-06-08`.
///   * **datetime**  — full local ISO-8601 `yyyy-MM-ddTHH:mm:ss` (carries a
///     time-of-day), e.g. `2026-06-08T17:00:00`.
///
/// `DateTime.parse` accepts both, so day-grouping keeps working on the date
/// part. These helpers are pure Dart (no Flutter, no intl) so they're trivially
/// unit-testable and safe to share between the parser and the widgets.
library;

/// True when [due] carries a time-of-day (a `T`-bearing ISO datetime) rather
/// than a bare `yyyy-MM-dd` calendar date.
bool dueDateHasTime(String? due) =>
    due != null && due.length > 10 && due.contains('T');

/// The `yyyy-MM-dd` calendar-date portion of [due] (drops any time component).
String dueDateDayPart(String due) =>
    due.length >= 10 ? due.substring(0, 10) : due;

/// The `(hour, minute)` of [due]'s time-of-day, or null when [due] is date-only
/// or unparseable. Hour is 0-23.
({int hour, int minute})? dueTimeParts(String? due) {
  if (!dueDateHasTime(due)) return null;
  // Convert to the device's local zone before reading the clock. A
  // server-normalised `reminderAt` (and any agent-created reminder) is stored
  // UTC-aware, so noon Madrid is `10:00Z` — reading `.hour` on the raw parse
  // showed 10:00. `.toLocal()` is a no-op on a naive (already-local) value, so
  // date-only / naive `dueDate` is unaffected; it only corrects UTC-aware input.
  final dt = DateTime.tryParse(due!)?.toLocal();
  if (dt == null) return null;
  return (hour: dt.hour, minute: dt.minute);
}

/// Formats an (hour 0-23, minute 0-59) pair as a 12-hour `h:mm AM/PM` label,
/// e.g. `5:00 PM`, `12:30 AM`. Intl-free so it's deterministic in tests.
String formatClock12(int hour, int minute) {
  final period = hour < 12 ? 'AM' : 'PM';
  var h = hour % 12;
  if (h == 0) h = 12;
  final mm = minute.toString().padLeft(2, '0');
  return '$h:$mm $period';
}

/// The `h:mm AM/PM` label for [due]'s time, or null when it has no time.
String? formatDueTimeLabel(String? due) {
  final parts = dueTimeParts(due);
  if (parts == null) return null;
  return formatClock12(parts.hour, parts.minute);
}

/// The local **calendar day** [due] falls on, as a date-only [DateTime] at
/// local midnight (`DateTime(y, m, d)`), or null when [due] is absent, empty,
/// or unparseable (never throws).
///
/// `.toLocal()`s the parsed instant before reading `.year/.month/.day` — a
/// UTC-aware [due] (the server emits this for a recurring template whose
/// anchor was tz-aware; see `tasks/store.py`) must resolve to the same
/// wall-clock day everywhere it's bucketed, or the same task reads as "today"
/// on the calendar and "yesterday" in the List view's overdue check. This is
/// the single implementation behind that day-derivation — previously
/// duplicated across `task_calendar_utils.dart`'s `expandRecurringForRange`
/// and `tasks_screen.dart`'s `_isOverdueOn`/`_groupTasks`. A no-op on an
/// already-naive/local value.
DateTime? localDueDay(String? due) {
  if (due == null || due.isEmpty) return null;
  final parsed = DateTime.tryParse(due);
  if (parsed == null) return null;
  final local = parsed.toLocal();
  return DateTime(local.year, local.month, local.day);
}

/// A human display for [due]: the date, plus ` · 5:00 PM` when a time is set.
String dueDateDisplay(String due) {
  final label = formatDueTimeLabel(due);
  final day = dueDateDayPart(due);
  return label == null ? day : '$day · $label';
}

/// The time-of-day label to show on a card's due chip, accounting for the
/// Smart-Reschedule shape where [due] is **date-only** but a separate
/// [reminderAt] carries the time-of-day (e.g. the 10:00 AM default).
///
/// * When [due] already carries a time → that time (datetime dueDates are
///   unchanged).
/// * When [due] is date-only but [reminderAt] has a time → the reminder's time.
/// * Otherwise → null (a bare calendar date with no time at all).
String? cardDueTimeLabel(String? due, String? reminderAt) {
  final ownTime = formatDueTimeLabel(due);
  if (ownTime != null) return ownTime;
  if (due == null || due.isEmpty) return null;
  return formatDueTimeLabel(reminderAt);
}

/// Composes a `dueDate` string from a calendar [day] plus an optional
/// time-of-day. With both [hour] and [minute] → a full local ISO datetime
/// `yyyy-MM-ddTHH:mm:00`; otherwise a date-only `yyyy-MM-dd` string.
String composeDueDate(DateTime day, {int? hour, int? minute}) {
  final d = '${day.year.toString().padLeft(4, '0')}-'
      '${day.month.toString().padLeft(2, '0')}-'
      '${day.day.toString().padLeft(2, '0')}';
  if (hour == null || minute == null) return d;
  return '${d}T${hour.toString().padLeft(2, '0')}:'
      '${minute.toString().padLeft(2, '0')}:00';
}
