part of '../smart_add_parser.dart';

// ── Recurrence ───────────────────────────────────────────────────────────────
//
// cron can't cleanly express "every N days/weeks", so those phrases are NOT
// recognized here — they stay in the title untouched.

// "every monday".."every sunday" and the short forms "every mon".."every sun"
// → weekly on that weekday (+ implies the next such weekday as the due day).
final RegExp _everyWeekday = RegExp(
  r'(^|\s)every\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)(?=\s|$)',
  caseSensitive: false,
);
// "every weekday" / "weekdays" → Mon-Fri.
final RegExp _everyWeekdays = RegExp(
  r'(^|\s)(?:every\s+weekday|weekdays)(?=\s|$)',
  caseSensitive: false,
);
// "every day" / "daily" / "everyday" → daily.
final RegExp _dailyWord = RegExp(
  r'(^|\s)(?:every\s+day|daily|everyday)(?=\s|$)',
  caseSensitive: false,
);
// "every week" / "weekly" → weekly.
final RegExp _weeklyWord = RegExp(
  r'(^|\s)(?:every\s+week|weekly)(?=\s|$)',
  caseSensitive: false,
);
// "every month" / "monthly" → monthly.
final RegExp _monthlyWord = RegExp(
  r'(^|\s)(?:every\s+month|monthly)(?=\s|$)',
  caseSensitive: false,
);
// "every year" / "yearly" / "annually" → yearly.
final RegExp _yearlyWord = RegExp(
  r'(^|\s)(?:every\s+year|yearly|annually)(?=\s|$)',
  caseSensitive: false,
);

/// Every recurrence matcher, run against the original input. All emit
/// `SmartTokenKind.recurrence` [Raw]s ranked `_rankRecurrence`.
///
/// "every monday" and a bare weekday-word date (`_weekdayWord` in
/// `dates.dart`) can both match inside the same input (e.g. "every monday"):
/// resolution is decided by `_resolveOverlaps`' sort in `smart_add_parser.dart`
/// — `_everyWeekday`'s span starts at "every", earlier than the bare
/// "monday" span, so the earliest-start rule alone picks it. `rank` is the
/// deterministic backstop for the rarer case of a genuine start+length tie.
void _collectRecurrence(_Collector c) {
  final today = c.today;

  c.scan(_everyWeekday, (m, s) {
    final wd = _weekdays[m.group(2)!.toLowerCase()];
    if (wd == null) return null;
    return Raw(
      s,
      m.end,
      SmartTokenKind.recurrence,
      rank: _rankRecurrence,
      recurrence: Recurrence(RecurrenceKind.weekly, weekday: wd),
      recurrenceDate: _weekdayDate(today, wd, nextWeek: false),
    );
  });
  // "every weekday" / "weekdays" → Mon-Fri. Before _dailyWord so "every
  // weekday" isn't half-swallowed.
  c.scan(
    _everyWeekdays,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.recurrence,
      rank: _rankRecurrence,
      recurrence: Recurrence.weekdays,
    ),
  );
  // "every day" / "daily" / "everyday" → daily.
  c.scan(
    _dailyWord,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.recurrence,
      rank: _rankRecurrence,
      recurrence: Recurrence.daily,
    ),
  );
  // "every week" / "weekly" → weekly (weekday resolved later from the due date).
  c.scan(
    _weeklyWord,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.recurrence,
      rank: _rankRecurrence,
      recurrence: const Recurrence(RecurrenceKind.weekly),
    ),
  );
  // "every month" / "monthly" → monthly.
  c.scan(
    _monthlyWord,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.recurrence,
      rank: _rankRecurrence,
      recurrence: Recurrence.monthly,
    ),
  );
  // "every year" / "yearly" / "annually" → yearly.
  c.scan(
    _yearlyWord,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.recurrence,
      rank: _rankRecurrence,
      recurrence: Recurrence.yearly,
    ),
  );
}
