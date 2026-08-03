part of '../smart_add_parser.dart';

// ── Dates ────────────────────────────────────────────────────────────────────

// Weekday name/abbreviation lookup, shared with `recurrence_patterns.dart`
// ("every monday" resolves its implied due day through this same table).
const Map<String, int> _weekdays = {
  'mon': 1,
  'monday': 1,
  'tue': 2,
  'tues': 2,
  'tuesday': 2,
  'wed': 3,
  'weds': 3,
  'wednesday': 3,
  'thu': 4,
  'thur': 4,
  'thurs': 4,
  'thursday': 4,
  'fri': 5,
  'friday': 5,
  'sat': 6,
  'saturday': 6,
  'sun': 7,
  'sunday': 7,
};

// G4: month name/abbreviation lookup for `_monthDay`/`_dayMonth` below.
const Map<String, int> _months = {
  'jan': 1,
  'january': 1,
  'feb': 2,
  'february': 2,
  'mar': 3,
  'march': 3,
  'apr': 4,
  'april': 4,
  'may': 5,
  'jun': 6,
  'june': 6,
  'jul': 7,
  'july': 7,
  'aug': 8,
  'august': 8,
  'sep': 9,
  'sept': 9,
  'september': 9,
  'oct': 10,
  'october': 10,
  'nov': 11,
  'november': 11,
  'dec': 12,
  'december': 12,
};

// The `?:`-grouped alternatives (e.g. `jan(?:uary)?`) mean the SHORT form
// always matches first for a short input and the regex engine only reaches
// for the long suffix when the text actually continues that way — no
// ordering hazard the way a flat `jan|january` list would have.
const String _monthPattern =
    r'jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|'
    r'aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?';

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
// G4: `D-M-YYYY` / `D.M.YYYY` / `D/M/YYYY` — the 4-digit year removes all
// ambiguity, so this is unconditionally day-first (the `\3` backreference
// forces the SAME separator on both sides, so "5-6.2026" doesn't match).
final RegExp _dmySeparatedDate = RegExp(
  r'(^|\s)(\d{1,2})([-./])(\d{1,2})\3(\d{4})(?=\s|$)',
);
// G4: "june 5" / "5 june" (+ optional ordinal suffix, captured as its own
// group so the callback can tell it was present — see `_ambiguousMonths`
// below — and/or a 4-digit year). The only genuinely missing capability
// before this: no way to type an absolute date except ISO or US M/D.
// "Marching band practice", "may need to call back", "a march to the sea",
// "summon the may queen" are saved by the required `\s+\d`: none of them has
// a digit directly touching the month word. That guard alone is NOT
// sufficient, though — see `_ambiguousMonths` for the digit-touching cases
// ("march 3 miles", "may 2 people attend") a reviewer caught.
final RegExp _monthDay = RegExp(
  r'(^|\s)(' +
      _monthPattern +
      r')\s+(\d{1,2})(st|nd|rd|th)?(?:,?\s+(\d{4}))?(?=\s|$)',
  caseSensitive: false,
);
final RegExp _dayMonth = RegExp(
  r'(^|\s)(\d{1,2})(st|nd|rd|th)?\s+(' +
      _monthPattern +
      r')(?:,?\s+(\d{4}))?(?=\s|$)',
  caseSensitive: false,
);

// `mar`/`march` (verb: "don't mar the finish", "we march 3 miles") and `may`
// (modal verb: "may 2 people attend") are the two month names that double as
// common English words — and unlike the four anti-patterns above, they break
// on a digit-touching sentence shape the required-`\s+\d` guard does NOT
// save ("march 3 miles", "may 2 people attend", "3 march to the coast"). For
// these two, `_monthDay`/`_dayMonth` additionally require ONE of: an ordinal
// suffix ("march 3rd"), an explicit 4-digit year ("march 3 2027"), or a
// leading cue word immediately before the whole match ("on march 3") —
// reusing `_weekdayCueWords` rather than a second hand-maintained list.
// Every other month name/abbreviation stays bare, exactly as before.
const Set<String> _ambiguousMonths = {'mar', 'march', 'may'};

/// Whether the text immediately before [matchStart] in [input] ends with a
/// cue word from `_weekdayCueWords` — the same disambiguation signal G1/G2
/// use for the restricted weekday short forms, reused here for
/// `_ambiguousMonths` instead of inventing a second list.
bool _precededByCueWord(String input, int matchStart) {
  final beforeTrim = input.substring(0, matchStart).trim();
  if (beforeTrim.isEmpty) return false;
  return _weekdayCueWords.contains(
    beforeTrim.split(_whitespace).last.toLowerCase(),
  );
}

final RegExp _inNDays = RegExp(
  r'(^|\s)in\s+(\d+)\s+days?(?=\s|$)',
  caseSensitive: false,
);
final RegExp _inNWeeks = RegExp(
  r'(^|\s)in\s+(\d+)\s+weeks?(?=\s|$)',
  caseSensitive: false,
);
// G3: "in 3 months" -> today + 3 CALENDAR months (day-of-month clamped by
// `_addMonths`, not silently rolled into a later month).
final RegExp _inNMonths = RegExp(
  r'(^|\s)in\s+(\d+)\s+months?(?=\s|$)',
  caseSensitive: false,
);
// `+3d`/`+3 days`, `+3w`/`+3 weeks` (G3), `+3m`/`+3 months` (G3) — a forward
// offset by day/week/calendar-month.
final RegExp _plusNUnit = RegExp(
  r'(^|\s)\+(\d+)\s*(d(?:ays?)?|w(?:eeks?)?|m(?:onths?)?)(?=\s|$)',
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
  r'(^|\s)(?:next|nxt)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|weds|thu|thur|thurs|fri|sat|sun)(?=\s|$)',
  caseSensitive: false,
);
final RegExp _tomorrow = RegExp(
  r'(^|\s)(tomorrow|tmrw|tmr|tom)(?=\s|$)',
  caseSensitive: false,
);
// "day after tomorrow" / "overmorrow" -> today + 2. Must be scanned so it
// out-ranks the plain `_tomorrow` match on the shared "tomorrow" substring
// (it does: `_dayAfterTomorrow`'s span starts at "day", strictly EARLIER
// than `_tomorrow`'s span starting at "tomorrow" — `_resolveOverlaps`'
// primary earliest-start rule alone picks it, no length/rank tiebreak
// needed). Before this, "day after tomorrow" matched bare `tomorrow`
// (wrong day) and stranded "day after" in the title.
final RegExp _dayAfterTomorrow = RegExp(
  r'(^|\s)(?:day\s+after\s+tomorrow|overmorrow)(?=\s|$)',
  caseSensitive: false,
);
// NOTE: `tonight` lives in the time family now (`smart_add/times.dart`) — it
// resolves to a concrete evening TIME (20:00), not just a bare date. `tn`/
// `tod`/`tdy` stay here, date-only.
final RegExp _todayWord = RegExp(
  r'(^|\s)(today|tdy|tod|tn)(?=\s|$)',
  caseSensitive: false,
);
final RegExp _yesterday = RegExp(
  r'(^|\s)yesterday(?=\s|$)',
  caseSensitive: false,
);
final RegExp _eod = RegExp(r'(^|\s)eod(?=\s|$)', caseSensitive: false);
final RegExp _eow = RegExp(r'(^|\s)eow(?=\s|$)', caseSensitive: false);
// G3: "eom" -> last day of the current month; "eoy" -> Dec 31 of the current
// year. Same `(?=\s|$)` tail as `_eod`/`_eow` already protects "eomish"/
// "eoyish" from matching mid-word.
final RegExp _eom = RegExp(r'(^|\s)eom(?=\s|$)', caseSensitive: false);
final RegExp _eoy = RegExp(r'(^|\s)eoy(?=\s|$)', caseSensitive: false);

// Weekday words are split into two matchers because three short forms read
// as ordinary English far more often than as a date:
//   - `sat` ("sat down", "sat nav"), `sun` ("sun is out"), `wed` ("wed the
//     bride") only count as a date when disambiguated by a cue: an
//     immediately preceding cue word (see `_weekdayCueWords`), an adjacent
//     clock token ("sat 5pm" / "5pm sat"), or being the ENTIRE input.
//   - every full weekday name, plus `mon`/`tue`/`tues`/`thu`/`thur`/`thurs`/
//     `fri`, don't collide with common English and stay unconditionally
//     bare. `weds` (G3) joins the restricted tier instead — it's the present
//     tense of "to wed" ("she weds him tomorrow"), the exact same verb
//     collision `wed` was restricted for, not just a Wednesday
//     abbreviation.
final RegExp _weekdayWordBare = RegExp(
  r'(^|\s)(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|thu|thur|thurs|fri)(?=\s|$)',
  caseSensitive: false,
);
final RegExp _weekdayWordCued = RegExp(
  r'(^|\s)(sat|sun|wed|weds)(?=\s|$)',
  caseSensitive: false,
);

// G2 #6: a cue word directly in front of ANY weekday (not just the
// restricted three) absorbs the cue into the token span too, so "by wed" /
// "due mon" clean up to nothing rather than stranding "by"/"due" in the
// title. Deliberately SHIPS WITHOUT `on`/`from` — those are high-frequency
// English ("turn on monday", "back from monday") and would eat a real word;
// they're held for a later pass. This naturally out-ranks both
// `_weekdayWordBare` and `_weekdayWordCued` on the same weekday (same date,
// but a longer span starting earlier at the cue word — the same
// earliest-start-wins mechanism `_nextWeekday` already relies on).
final RegExp _cuedWeekday = RegExp(
  r'(^|\s)(?:by|due|before|until|til|this|coming)\s+'
  r'(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|weds|thu|thur|thurs|fri|sat|sun)(?=\s|$)',
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
    // G4: try M/D first (the historical American default) and keep it when
    // valid; only fall back to D/M when M/D is impossible. This rescues
    // "15/03"/"25/12" for a European user with no setting to flip, while
    // leaving every currently-valid M/D reading (e.g. pinned "6/10") exactly
    // as it was.
    final asMonthDay = _safeDate(today.year, int.parse(g2), int.parse(g3));
    if (asMonthDay != null) {
      return Raw(
        s,
        m.end,
        SmartTokenKind.date,
        rank: _rankExplicitDate,
        date: asMonthDay,
      );
    }
    final asDayMonth = _safeDate(today.year, int.parse(g3), int.parse(g2));
    return asDayMonth == null
        ? null
        : Raw(
            s,
            m.end,
            SmartTokenKind.date,
            rank: _rankExplicitDate,
            date: asDayMonth,
          );
  });
  c.scan(_dmySeparatedDate, (m, s) {
    final day = int.parse(m.group(2)!);
    final month = int.parse(m.group(4)!);
    final year = int.parse(m.group(5)!);
    final d = _safeDate(year, month, day);
    return d == null
        ? null
        : Raw(s, m.end, SmartTokenKind.date, rank: _rankExplicitDate, date: d);
  });
  c.scan(_monthDay, (m, s) {
    final monthWord = m.group(2)!.toLowerCase();
    final month = _months[monthWord];
    if (month == null) return null;
    final day = int.parse(m.group(3)!);
    final hasOrdinal = m.group(4) != null;
    final explicitYear = m.group(5) != null ? int.parse(m.group(5)!) : null;
    if (_ambiguousMonths.contains(monthWord) &&
        !hasOrdinal &&
        explicitYear == null &&
        !_precededByCueWord(c.input, s)) {
      return null; // "march 3 miles" / "may 2 people attend" etc.
    }
    final year = explicitYear ?? _defaultYearFor(today, month, day);
    final d = _safeDate(year, month, day);
    return d == null
        ? null
        : Raw(s, m.end, SmartTokenKind.date, rank: _rankExplicitDate, date: d);
  });
  c.scan(_dayMonth, (m, s) {
    final day = int.parse(m.group(2)!);
    final hasOrdinal = m.group(3) != null;
    final monthWord = m.group(4)!.toLowerCase();
    final month = _months[monthWord];
    if (month == null) return null;
    final explicitYear = m.group(5) != null ? int.parse(m.group(5)!) : null;
    if (_ambiguousMonths.contains(monthWord) &&
        !hasOrdinal &&
        explicitYear == null &&
        !_precededByCueWord(c.input, s)) {
      return null; // "3 march to the coast" etc.
    }
    final year = explicitYear ?? _defaultYearFor(today, month, day);
    final d = _safeDate(year, month, day);
    return d == null
        ? null
        : Raw(s, m.end, SmartTokenKind.date, rank: _rankDayMonth, date: d);
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
  c.scan(_plusNUnit, (m, s) {
    final n = int.parse(m.group(2)!);
    final unit = m.group(3)!.toLowerCase();
    final date = unit.startsWith('w')
        ? today.add(Duration(days: 7 * n))
        : unit.startsWith('m')
        ? _addMonths(today, n)
        : today.add(Duration(days: n));
    return Raw(
      s,
      m.end,
      SmartTokenKind.date,
      rank: _rankRelativeDate,
      date: date,
    );
  });
  c.scan(
    _inNMonths,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.date,
      rank: _rankRelativeDate,
      date: _addMonths(today, int.parse(m.group(2)!)),
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
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.date,
      rank: _rankRelativeDate,
      date: today,
    ),
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
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.date,
      rank: _rankRelativeDate,
      date: today,
    ),
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
  c.scan(
    _eom,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.date,
      rank: _rankRelativeDate,
      date: DateTime(today.year, today.month + 1, 0), // last day of this month
    ),
  );
  c.scan(
    _eoy,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.date,
      rank: _rankRelativeDate,
      date: DateTime(today.year, 12, 31),
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
  c.scan(_cuedWeekday, (m, s) {
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

/// Adds [months] calendar months to [date], clamping the day-of-month to the
/// target month's length rather than letting it silently roll into a LATER
/// month the way `DateTime(year, month, day)` does on its own (e.g. Jan 31 +
/// 1 month would otherwise silently become Mar 3 instead of Feb 28/29 — the
/// same class of silent lie `_safeDate` exists to prevent for M/D dates).
DateTime _addMonths(DateTime date, int months) {
  final totalMonths = date.month - 1 + months;
  final year = date.year + totalMonths ~/ 12;
  final month = totalMonths % 12 + 1;
  final lastDayOfMonth = DateTime(year, month + 1, 0).day;
  final day = date.day > lastDayOfMonth ? lastDayOfMonth : date.day;
  return DateTime(year, month, day);
}

/// G4: the year for a `_monthDay`/`_dayMonth` match that carried no explicit
/// year — the next occurrence of [month]/[day] on or after [today]. Tries
/// THIS year first (via `_safeDate`, so an impossible combination like a
/// non-leap Feb 29 never silently rolls over); falls back to next year both
/// when this year's occurrence has already passed and when this year's
/// combination is flat-out invalid (in which case the final `_safeDate` call
/// in the caller rejects the match if next year is ALSO invalid).
int _defaultYearFor(DateTime today, int month, int day) {
  final thisYear = _safeDate(today.year, month, day);
  if (thisYear != null && !thisYear.isBefore(today)) return today.year;
  return today.year + 1;
}
