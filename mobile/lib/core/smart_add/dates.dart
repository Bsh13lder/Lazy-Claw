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
// M/D calendar date. Two guards keep this off ordinary fraction-shaped text:
//   - a negative VARIABLE-LENGTH lookbehind rejects a preceding "chapter"/
//     "page"/etc — deliberately variable-length (V8-backed regex engine;
//     this works fine in Dart, don't "simplify" it into something fixed-width).
//   - the callback below additionally requires at least one side to have
//     exactly 2 digits, so single-digit/single-digit fractions ("1/2", "3/4")
//     never even reach the lookbehind.
final RegExp _mdDate = RegExp(
  r'(^|\s)(?<!\b(?:chapter|page|part|section|step|round|ratio|version|v|split|half)\s)(\d{1,2})/(\d{1,2})(?=\s|$)',
  caseSensitive: false,
);
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
// "day after tomorrow" / "overmorrow" -> today + 2. Must be scanned so it
// out-ranks the plain `_tomorrow` match on the shared "tomorrow" substring
// (it does: same start, longer span — `_resolveOverlaps` picks the longer
// on a start tie). Before this, "day after tomorrow" matched bare
// `tomorrow` (wrong day) and stranded "day after" in the title.
final RegExp _dayAfterTomorrow = RegExp(
  r'(^|\s)(?:day\s+after\s+tomorrow|overmorrow)(?=\s|$)',
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

// Weekday words are split into two matchers because three short forms read
// as ordinary English far more often than as a date:
//   - `sat` ("sat down", "sat nav"), `sun` ("sun is out"), `wed` ("wed the
//     bride") only count as a date when disambiguated by a cue: an
//     immediately preceding cue word (see `_weekdayCueWords`), an adjacent
//     clock token ("sat 5pm" / "5pm sat"), or being the ENTIRE input.
//   - every full weekday name, plus `mon`/`tue`/`thu`/`fri`, don't collide
//     with common English and stay unconditionally bare.
final RegExp _weekdayWordBare = RegExp(
  r'(^|\s)(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|thu|fri)(?=\s|$)',
  caseSensitive: false,
);
final RegExp _weekdayWordCued = RegExp(
  r'(^|\s)(sat|sun|wed)(?=\s|$)',
  caseSensitive: false,
);

const Set<String> _weekdayCueWords = {
  'on',
  'by',
  'due',
  'this',
  'next',
  'coming',
  'every',
  'before',
  'until',
  'til',
  'from',
};

// A standalone clock-ish token, tested against the single word immediately
// before/after a restricted weekday abbreviation (no shared `(^|\s)`/
// `(?=\s|$)` boundary groups needed — the caller already isolated the word).
final RegExp _standaloneClockWord = RegExp(
  r'^(?:\d{1,2}(?::\d{2})?(?:am|pm|a|p)|(?:[01]?\d|2[0-3]):[0-5]\d)$',
  caseSensitive: false,
);

/// Whether a restricted weekday abbreviation (`sat`/`sun`/`wed`) at
/// `[wordStart, wordEnd)` in `input` is disambiguated enough to count as a
/// date: an immediately preceding cue word, an adjacent clock token on
/// either side, or being the whole (trimmed) input.
bool _weekdayCueSatisfied(String input, int wordStart, int wordEnd) {
  final beforeTrim = input.substring(0, wordStart).trim();
  final afterTrim = input.substring(wordEnd).trim();
  if (beforeTrim.isEmpty && afterTrim.isEmpty) return true; // sole input
  if (beforeTrim.isNotEmpty) {
    final prevWord = beforeTrim.split(_whitespace).last;
    if (_weekdayCueWords.contains(prevWord.toLowerCase())) return true;
    if (_standaloneClockWord.hasMatch(prevWord)) return true;
  }
  if (afterTrim.isNotEmpty) {
    final nextWord = afterTrim.split(_whitespace).first;
    if (_standaloneClockWord.hasMatch(nextWord)) return true;
  }
  return false;
}

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
    final g2 = m.group(2)!;
    final g3 = m.group(3)!;
    if (g2.length != 2 && g3.length != 2) {
      return null; // require >=1 two-digit component (kills "1/2", "3/4")
    }
    final d = _safeDate(today.year, int.parse(g2), int.parse(g3));
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
    _dayAfterTomorrow,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.date,
      rank: _rankRelativeDate,
      date: today.add(const Duration(days: 2)),
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
  c.scan(_weekdayWordBare, (m, s) {
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
  c.scan(_weekdayWordCued, (m, s) {
    if (!_weekdayCueSatisfied(c.input, s, m.end)) return null;
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
