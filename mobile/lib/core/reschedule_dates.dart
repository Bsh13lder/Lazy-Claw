/// Pure date math for the Smart Fast Reschedule flow.
///
/// Mirrors the web helper `web/src/components/tasks/rescheduleDates.ts`. All
/// functions take `now` explicitly (no hidden [DateTime.now]) so they're
/// trivially unit-testable, and they operate in the user's **local** time.
///
/// The day-resolving functions ([tomorrow], [thisWeekend], [nextWeek]) return a
/// date-only `DateTime` (midnight local). [composeAt] then turns a target day +
/// a time-of-day into the dual-shape strings the Tasks API expects:
///   * `dueDate` — a bare `YYYY-MM-DD` calendar date (keeps the backend's
///     `due_date <= today_str` overdue/bucket comparison lexically correct), and
///   * `reminderAt` — that day at the chosen time as a local ISO datetime
///     `YYYY-MM-DDTHH:MM:00` (this carries the "10am" and fires the nag).
library;

/// Strip [dt] down to a date-only [DateTime] (midnight, local).
DateTime _dayOf(DateTime dt) => DateTime(dt.year, dt.month, dt.day);

/// The day after [now] (date-only, local).
DateTime tomorrow(DateTime now) =>
    _dayOf(now).add(const Duration(days: 1));

/// The coming Saturday (date-only, local).
///
/// If [now] *is* Saturday → today; if Sunday → the next Saturday (6 days out);
/// otherwise the Saturday later this week.
DateTime thisWeekend(DateTime now) {
  final today = _dayOf(now);
  // [DateTime.weekday]: Mon=1 … Sat=6, Sun=7.
  final delta = (DateTime.saturday - today.weekday) % 7;
  return today.add(Duration(days: delta));
}

/// The coming Monday (date-only, local).
///
/// If [now] *is* Monday → the next Monday (7 days out); otherwise the next
/// Monday strictly after today.
DateTime nextWeek(DateTime now) {
  final today = _dayOf(now);
  var delta = (DateTime.monday - today.weekday) % 7;
  // Always land on a Monday strictly after today (today-is-Monday → +7).
  if (delta == 0) delta = 7;
  return today.add(Duration(days: delta));
}

/// The dual-shape due strings produced by [composeAt]: a date-only [dueDate]
/// and a same-day-at-time [reminderAt].
class RescheduleTarget {
  const RescheduleTarget({required this.dueDate, required this.reminderAt});

  /// Date-only `YYYY-MM-DD`.
  final String dueDate;

  /// Local ISO datetime `YYYY-MM-DDTHH:MM:00`.
  final String reminderAt;
}

String _pad2(int v) => v.toString().padLeft(2, '0');

String _isoDay(DateTime day) =>
    '${day.year.toString().padLeft(4, '0')}-${_pad2(day.month)}-${_pad2(day.day)}';

/// Compose a [RescheduleTarget] for [day] at [hour]:[minute] (default 10:00).
///
/// [dueDate] is the date-only `YYYY-MM-DD` of [day]; [reminderAt] is that same
/// day at the given local time. Both are zero-padded.
RescheduleTarget composeAt(DateTime day, {int hour = 10, int minute = 0}) {
  final d = _isoDay(day);
  return RescheduleTarget(
    dueDate: d,
    reminderAt: '${d}T${_pad2(hour)}:${_pad2(minute)}:00',
  );
}
