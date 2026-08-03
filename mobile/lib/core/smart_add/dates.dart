part of '../smart_add_parser.dart';

// ── Dates ────────────────────────────────────────────────────────────────────

// Weekday name/abbreviation lookup, shared with `recurrence_patterns.dart`
// ("every monday" resolves its implied due day through this same table).
const Map<String, int> _weekdays = {
  'mon': 1,
  'monday': 1,
  'tue': 2,
  'tuesday': 2,
  'wed': 3,
  'wednesday': 3,
  'thu': 4,
  'thursday': 4,
  'fri': 5,
  'friday': 5,
  'sat': 6,
  'saturday': 6,
  'sun': 7,
  'sunday': 7,
};

final RegExp _isoDate = RegExp(r'(^|\s)(\d{4})-(\d{2})-(\d{2})(?=\s|$)');
final RegExp _mdDate = RegExp(r'(^|\s)(\d{1,2})/(\d{1,2})(?=\s|$)');
final RegExp _inNDays = RegExp(
  r'(^|\s)in\s+(\d+)\s+days?(?=\s|$)',
  caseSensitive: false,
);
final RegExp _inNWeeks = RegExp(
  r'(^|\s)in\s+(\d+)\s+weeks?(?=\s|$)',
  caseSensitive: false,
);
// `+3d`, `+3 days`, `+3 day` — a forward day offset.
final RegExp _plusNDays = RegExp(
  r'(^|\s)\+(\d+)\s*d(?:ays?)?(?=\s|$)',
  caseSensitive: false,
);
final RegExp _nextWeek = RegExp(
  r'(^|\s)(?:next|nxt)\s+week(?=\s|$)',
  caseSensitive: false,
);
final RegExp _nextMonth = RegExp(
  r'(^|\s)(?:next|nxt)\s+month(?=\s|$)',
  caseSensitive: false,
);
final RegExp _nextYear = RegExp(
  r'(^|\s)(?:next|nxt)\s+year(?=\s|$)',
  caseSensitive: false,
);
final RegExp _thisWeekend = RegExp(
  r'(^|\s)this\s+weekend(?=\s|$)',
  caseSensitive: false,
);
final RegExp _nextWeekend = RegExp(
  r'(^|\s)(?:next|nxt)\s+weekend(?=\s|$)',
  caseSensitive: false,
);
final RegExp _nextWeekday = RegExp(
  r'(^|\s)(?:next|nxt)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)(?=\s|$)',
  caseSensitive: false,
);
final RegExp _tomorrow = RegExp(
  r'(^|\s)(tomorrow|tmrw|tmr|tom)(?=\s|$)',
  caseSensitive: false,
);
final RegExp _todayWord = RegExp(
  r'(^|\s)(today|tonight|tdy|tod|tn)(?=\s|$)',
  caseSensitive: false,
);
final RegExp _yesterday = RegExp(
  r'(^|\s)yesterday(?=\s|$)',
  caseSensitive: false,
);
final RegExp _eod = RegExp(r'(^|\s)eod(?=\s|$)', caseSensitive: false);
final RegExp _eow = RegExp(r'(^|\s)eow(?=\s|$)', caseSensitive: false);
final RegExp _weekdayWord = RegExp(
  r'(^|\s)(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)(?=\s|$)',
  caseSensitive: false,
);

/// Every date matcher, run against the original input. Emits
/// `SmartTokenKind.date` [Raw]s ranked `_rankExplicitDate` (ISO / M-D
/// calendar dates) or `_rankRelativeDate` (everything resolved relative to
/// `today`/`ref`).
void _collectDates(_Collector c) {
  final today = c.today;

  c.scan(_isoDate, (m, s) {
    final d = _safeDate(
      int.parse(m.group(2)!),
      int.parse(m.group(3)!),
      int.parse(m.group(4)!),
    );
    return d == null
        ? null
        : Raw(s, m.end, SmartTokenKind.date, rank: _rankExplicitDate, date: d);
  });
  c.scan(_mdDate, (m, s) {
    final d = _safeDate(
      today.year,
      int.parse(m.group(2)!),
      int.parse(m.group(3)!),
    );
    return d == null
        ? null
        : Raw(s, m.end, SmartTokenKind.date, rank: _rankExplicitDate, date: d);
  });
  c.scan(
    _inNDays,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.date,
      rank: _rankRelativeDate,
      date: today.add(Duration(days: int.parse(m.group(2)!))),
    ),
  );
  c.scan(
    _inNWeeks,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.date,
      rank: _rankRelativeDate,
      date: today.add(Duration(days: 7 * int.parse(m.group(2)!))),
    ),
  );
  c.scan(
    _plusNDays,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.date,
      rank: _rankRelativeDate,
      date: today.add(Duration(days: int.parse(m.group(2)!))),
    ),
  );
  c.scan(
    _nextWeek,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.date,
      rank: _rankRelativeDate,
      date: today.add(const Duration(days: 7)),
    ),
  );
  c.scan(
    _nextMonth,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.date,
      rank: _rankRelativeDate,
      date: DateTime(today.year, today.month + 1, 1),
    ),
  );
  c.scan(
    _nextYear,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.date,
      rank: _rankRelativeDate,
      date: DateTime(today.year + 1, 1, 1),
    ),
  );
  c.scan(
    _thisWeekend,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.date,
      rank: _rankRelativeDate,
      date: _weekdayDate(today, DateTime.saturday, nextWeek: false),
    ),
  );
  c.scan(
    _nextWeekend,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.date,
      rank: _rankRelativeDate,
      date: _weekdayDate(today, DateTime.saturday, nextWeek: true),
    ),
  );
  c.scan(_nextWeekday, (m, s) {
    final wd = _weekdays[m.group(2)!.toLowerCase()];
    if (wd == null) return null;
    return Raw(
      s,
      m.end,
      SmartTokenKind.date,
      rank: _rankRelativeDate,
      date: _weekdayDate(today, wd, nextWeek: true),
    );
  });
  c.scan(
    _tomorrow,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.date,
      rank: _rankRelativeDate,
      date: today.add(const Duration(days: 1)),
    ),
  );
  c.scan(
    _todayWord,
    (m, s) =>
        Raw(s, m.end, SmartTokenKind.date, rank: _rankRelativeDate, date: today),
  );
  c.scan(
    _yesterday,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.date,
      rank: _rankRelativeDate,
      date: today.subtract(const Duration(days: 1)),
    ),
  );
  c.scan(
    _eod,
    (m, s) =>
        Raw(s, m.end, SmartTokenKind.date, rank: _rankRelativeDate, date: today),
  );
  c.scan(
    _eow,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.date,
      rank: _rankRelativeDate,
      date: _weekdayDate(today, DateTime.sunday, nextWeek: false),
    ),
  );
  c.scan(_weekdayWord, (m, s) {
    final wd = _weekdays[m.group(2)!.toLowerCase()];
    if (wd == null) return null;
    return Raw(
      s,
      m.end,
      SmartTokenKind.date,
      rank: _rankRelativeDate,
      date: _weekdayDate(today, wd, nextWeek: false),
    );
  });
}
