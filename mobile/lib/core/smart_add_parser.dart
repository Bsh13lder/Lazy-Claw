/// On-device, offline, zero-LLM "Todoist/TickTick-style" smart-add parsing.
///
/// Given a raw task title typed by the user, [parseSmartAdd] pulls out
/// natural-language tokens for due date, time-of-day, priority, and project,
/// strips them from the title, and returns the cleaned title alongside the
/// parsed values plus the exact character [SmartToken] spans (so the UI can
/// highlight recognized tokens live, in-field).
///
/// Every matcher runs against the *original* input string, so token offsets map
/// straight back to what the user typed. Accepted matches are de-overlapped
/// (earliest start wins; longest wins on a tie) and a token can only be one
/// kind. The clean title is the original minus the union of accepted spans, with
/// whitespace collapsed.
///
/// It is pure Dart (no I/O, no async) and never throws — unrecognized tokens are
/// simply left in the title.
library;

import 'due_date.dart';
import 'recurrence.dart';

/// What a recognized [SmartToken] represents. Drives the live highlight color.
enum SmartTokenKind { date, time, priority, project, recurrence }

/// A recognized token's half-open character range `[start, end)` into the
/// *original* input string, plus its [kind]. Immutable.
class SmartToken {
  final int start;
  final int end;
  final SmartTokenKind kind;
  const SmartToken(this.start, this.end, this.kind);
}

/// The structured result of parsing a smart-add title.
class ParsedTask {
  /// The title with every recognized token removed and whitespace collapsed.
  final String cleanTitle;

  /// Either a date-only `yyyy-MM-dd` string, or — when a time-of-day token was
  /// recognized — a full local ISO datetime `yyyy-MM-ddTHH:mm:00`. Null when no
  /// date or time token was found.
  final String? dueDate;

  /// True when [dueDate] carries a time-of-day (an ISO datetime, not a bare
  /// calendar date). A bare time like `5pm` resolves to today + that time.
  final bool hasTime;

  /// One of `low | medium | high | urgent`, or null.
  final String? priority;

  /// Project / category name, or null.
  final String? project;

  /// The recognized recurrence (e.g. "every monday" → weekly on Monday), or
  /// null when no recurrence vocabulary was found. [RecurrenceKind.none] is
  /// never produced here — a non-match leaves this null. Convert to a cron via
  /// [recurrenceToCron] (passing the composed due date as the anchor).
  final Recurrence? recurrence;

  /// The accepted token spans, sorted by [SmartToken.start], non-overlapping,
  /// each indexing into the *original* input string. Empty when nothing matched.
  final List<SmartToken> tokens;

  const ParsedTask({
    required this.cleanTitle,
    this.dueDate,
    this.hasTime = false,
    this.priority,
    this.project,
    this.recurrence,
    this.tokens = const [],
  });
}

/// Parse [input] into a [ParsedTask]. [now] is injectable for deterministic
/// tests; it defaults to [DateTime.now]. A time-of-day token (`5pm`, `17:00`,
/// `9a`, `morning`, `in 2h`) is kept: it combines with any date token (or today,
/// when only a time is given) into a full ISO datetime; otherwise the due date
/// stays date-only.
ParsedTask parseSmartAdd(String input, {DateTime? now}) {
  final ref = now ?? DateTime.now();
  final today = DateTime(ref.year, ref.month, ref.day);

  final accepted = _resolveOverlaps(_collect(input, ref, today));

  // Earliest date supplies the day; earliest time supplies the clock; first
  // priority / project win. (Accepted spans are already sorted by start.)
  DateTime? day;
  int? hour;
  int? minute;
  DateTime? timeDate; // date override carried by `in Nh` (may cross midnight)
  String? priority;
  String? project;
  Recurrence? recurrence;
  DateTime?
  recurrenceDay; // implied due day from "every monday" (no explicit date)
  for (final r in accepted) {
    switch (r.kind) {
      case SmartTokenKind.date:
        day ??= r.date;
      case SmartTokenKind.time:
        if (hour == null) {
          hour = r.hour;
          minute = r.minute;
          timeDate = r.timeDate;
        }
      case SmartTokenKind.priority:
        priority ??= r.priority;
      case SmartTokenKind.project:
        project ??= r.project;
      case SmartTokenKind.recurrence:
        if (recurrence == null) {
          recurrence = r.recurrence;
          recurrenceDay = r.recurrenceDate;
        }
    }
  }

  // "every monday" implies the next Monday as the due day when the user gave no
  // explicit date token — so the first occurrence lands correctly.
  day ??= recurrenceDay;

  String? value;
  var hasTime = false;
  if (day != null) {
    value = hour != null
        ? composeDueDate(day, hour: hour, minute: minute)
        : composeDueDate(day);
    hasTime = hour != null;
  } else if (hour != null) {
    value = composeDueDate(timeDate ?? today, hour: hour, minute: minute);
    hasTime = true;
  }

  // For a bare "weekly" / "every week" (no pinned weekday), anchor the cron's
  // day-of-week to the parsed due day when present, else today's weekday — so
  // recurrenceToCron emits a concrete `* * <dow>` rather than defaulting.
  if (recurrence != null &&
      recurrence.kind == RecurrenceKind.weekly &&
      recurrence.weekday == null) {
    recurrence = recurrence.copyWith(weekday: (day ?? today).weekday);
  }

  // Build the clean title by removing accepted spans right-to-left (so earlier
  // removals don't shift later indices), then collapsing whitespace.
  var stripped = input;
  for (final r in [...accepted]..sort((a, b) => b.start.compareTo(a.start))) {
    stripped = stripped.replaceRange(r.start, r.end, ' ');
  }
  final cleanTitle = stripped.replaceAll(_whitespace, ' ').trim();

  return ParsedTask(
    cleanTitle: cleanTitle,
    dueDate: value,
    hasTime: hasTime,
    priority: priority,
    project: project,
    recurrence: recurrence,
    tokens: [for (final r in accepted) SmartToken(r.start, r.end, r.kind)],
  );
}

// ── internals ────────────────────────────────────────────────────────────────

final RegExp _whitespace = RegExp(r'\s+');

/// An accepted raw match: its token span plus whichever payload its kind needs.
class _Raw {
  final int start;
  final int end;
  final SmartTokenKind kind;
  final DateTime? date; // date kind
  final int? hour; // time kind
  final int? minute; // time kind
  final DateTime? timeDate; // time kind: optional day override (in Nh)
  final String? priority; // priority kind
  final String? project; // project kind
  final Recurrence? recurrence; // recurrence kind
  final DateTime?
  recurrenceDate; // recurrence kind: implied due date ("every monday")
  const _Raw(
    this.start,
    this.end,
    this.kind, {
    this.date,
    this.hour,
    this.minute,
    this.timeDate,
    this.priority,
    this.project,
    this.recurrence,
    this.recurrenceDate,
  });
  int get length => end - start;
}

/// Greedy earliest-then-longest de-overlap. Returns spans sorted by start, with
/// no two overlapping (a character can belong to at most one token / kind).
List<_Raw> _resolveOverlaps(List<_Raw> raws) {
  final sorted = [...raws]
    ..sort((a, b) {
      if (a.start != b.start) return a.start.compareTo(b.start);
      return b.length.compareTo(a.length); // longest first on a tie
    });
  final accepted = <_Raw>[];
  var lastEnd = -1;
  for (final r in sorted) {
    if (r.start >= lastEnd) {
      accepted.add(r);
      lastEnd = r.end;
    }
  }
  return accepted;
}

/// Run every matcher against the original [input] and collect raw hits. Project
/// only ever contributes its FIRST hit (later `#tags` stay in the title).
List<_Raw> _collect(String input, DateTime ref, DateTime today) {
  final raws = <_Raw>[];

  // Token start excludes the leading `(^|\s)` boundary group (group 1) so a
  // highlight covers exactly the token characters, not the preceding space.
  int ts(RegExpMatch m) => m.start + (m.group(1)?.length ?? 0);

  void scan(RegExp re, _Raw? Function(RegExpMatch m, int start) f) {
    for (final m in re.allMatches(input)) {
      final r = f(m, ts(m));
      if (r != null) raws.add(r);
    }
  }

  // ── Dates ──────────────────────────────────────────────────────────────
  scan(_isoDate, (m, s) {
    final d = _safeDate(
      int.parse(m.group(2)!),
      int.parse(m.group(3)!),
      int.parse(m.group(4)!),
    );
    return d == null ? null : _Raw(s, m.end, SmartTokenKind.date, date: d);
  });
  scan(_mdDate, (m, s) {
    final d = _safeDate(
      today.year,
      int.parse(m.group(2)!),
      int.parse(m.group(3)!),
    );
    return d == null ? null : _Raw(s, m.end, SmartTokenKind.date, date: d);
  });
  scan(
    _inNDays,
    (m, s) => _Raw(
      s,
      m.end,
      SmartTokenKind.date,
      date: today.add(Duration(days: int.parse(m.group(2)!))),
    ),
  );
  scan(
    _inNWeeks,
    (m, s) => _Raw(
      s,
      m.end,
      SmartTokenKind.date,
      date: today.add(Duration(days: 7 * int.parse(m.group(2)!))),
    ),
  );
  scan(
    _plusNDays,
    (m, s) => _Raw(
      s,
      m.end,
      SmartTokenKind.date,
      date: today.add(Duration(days: int.parse(m.group(2)!))),
    ),
  );
  scan(
    _nextWeek,
    (m, s) => _Raw(
      s,
      m.end,
      SmartTokenKind.date,
      date: today.add(const Duration(days: 7)),
    ),
  );
  scan(
    _nextMonth,
    (m, s) => _Raw(
      s,
      m.end,
      SmartTokenKind.date,
      date: DateTime(today.year, today.month + 1, 1),
    ),
  );
  scan(
    _nextYear,
    (m, s) => _Raw(
      s,
      m.end,
      SmartTokenKind.date,
      date: DateTime(today.year + 1, 1, 1),
    ),
  );
  scan(
    _thisWeekend,
    (m, s) => _Raw(
      s,
      m.end,
      SmartTokenKind.date,
      date: _weekdayDate(today, DateTime.saturday, nextWeek: false),
    ),
  );
  scan(
    _nextWeekend,
    (m, s) => _Raw(
      s,
      m.end,
      SmartTokenKind.date,
      date: _weekdayDate(today, DateTime.saturday, nextWeek: true),
    ),
  );
  scan(_nextWeekday, (m, s) {
    final wd = _weekdays[m.group(2)!.toLowerCase()];
    if (wd == null) return null;
    return _Raw(
      s,
      m.end,
      SmartTokenKind.date,
      date: _weekdayDate(today, wd, nextWeek: true),
    );
  });
  scan(
    _tomorrow,
    (m, s) => _Raw(
      s,
      m.end,
      SmartTokenKind.date,
      date: today.add(const Duration(days: 1)),
    ),
  );
  scan(_todayWord, (m, s) => _Raw(s, m.end, SmartTokenKind.date, date: today));
  scan(
    _yesterday,
    (m, s) => _Raw(
      s,
      m.end,
      SmartTokenKind.date,
      date: today.subtract(const Duration(days: 1)),
    ),
  );
  scan(_eod, (m, s) => _Raw(s, m.end, SmartTokenKind.date, date: today));
  scan(
    _eow,
    (m, s) => _Raw(
      s,
      m.end,
      SmartTokenKind.date,
      date: _weekdayDate(today, DateTime.sunday, nextWeek: false),
    ),
  );
  scan(_weekdayWord, (m, s) {
    final wd = _weekdays[m.group(2)!.toLowerCase()];
    if (wd == null) return null;
    return _Raw(
      s,
      m.end,
      SmartTokenKind.date,
      date: _weekdayDate(today, wd, nextWeek: false),
    );
  });

  // ── Times ──────────────────────────────────────────────────────────────
  scan(_clock12, (m, s) {
    var h = int.parse(m.group(2)!);
    if (h < 1 || h > 12) return null; // not a valid 12-hour clock
    final min = m.group(3) != null ? int.parse(m.group(3)!.substring(1)) : 0;
    if (min > 59) return null;
    final ap = m.group(4)!.toLowerCase();
    final pm = ap == 'pm' || ap == 'p';
    if (h == 12) h = 0; // 12am -> 0; 12pm -> 0, then +12 below
    if (pm) h += 12;
    return _Raw(s, m.end, SmartTokenKind.time, hour: h, minute: min);
  });
  scan(
    _clock24,
    (m, s) => _Raw(
      s,
      m.end,
      SmartTokenKind.time,
      hour: int.parse(m.group(2)!),
      minute: int.parse(m.group(3)!),
    ),
  );
  scan(_inNHours, (m, s) {
    final target = ref.add(Duration(hours: int.parse(m.group(2)!)));
    return _Raw(
      s,
      m.end,
      SmartTokenKind.time,
      hour: target.hour,
      minute: target.minute,
      timeDate: DateTime(target.year, target.month, target.day),
    );
  });
  scan(_timeOfDay, (m, s) {
    final h = _timesOfDay[m.group(2)!.toLowerCase()];
    if (h == null) return null;
    return _Raw(s, m.end, SmartTokenKind.time, hour: h, minute: 0);
  });

  // ── Priority ───────────────────────────────────────────────────────────
  scan(
    _priorityCode,
    (m, s) => _Raw(
      s,
      m.end,
      SmartTokenKind.priority,
      priority: _priorityByCode[m.group(2)!],
    ),
  );
  scan(
    _priorityBare,
    (m, s) => _Raw(
      s,
      m.end,
      SmartTokenKind.priority,
      priority: _priorityByCode[m.group(2)!],
    ),
  );
  scan(_priorityBangs, (m, s) {
    final n = m.group(2)!.length;
    return _Raw(
      s,
      m.end,
      SmartTokenKind.priority,
      priority: n == 3 ? 'urgent' : (n == 2 ? 'high' : 'medium'),
    );
  });

  // ── Recurrence ───────────────────────────────────────────────────────────
  // "every <weekday>" → weekly on that weekday, AND (when no explicit date was
  // given) implies the next such weekday as the due day. Listed first so its
  // longer span wins the de-overlap over a bare weekday-word date.
  scan(_everyWeekday, (m, s) {
    final wd = _weekdays[m.group(2)!.toLowerCase()];
    if (wd == null) return null;
    return _Raw(
      s,
      m.end,
      SmartTokenKind.recurrence,
      recurrence: Recurrence(RecurrenceKind.weekly, weekday: wd),
      recurrenceDate: _weekdayDate(today, wd, nextWeek: false),
    );
  });
  // "every weekday" / "weekdays" → Mon-Fri. Before _dailyWord so "every
  // weekday" isn't half-swallowed.
  scan(
    _everyWeekdays,
    (m, s) => _Raw(
      s,
      m.end,
      SmartTokenKind.recurrence,
      recurrence: Recurrence.weekdays,
    ),
  );
  // "every day" / "daily" / "everyday" → daily.
  scan(
    _dailyWord,
    (m, s) =>
        _Raw(s, m.end, SmartTokenKind.recurrence, recurrence: Recurrence.daily),
  );
  // "every week" / "weekly" → weekly (weekday resolved later from the due date).
  scan(
    _weeklyWord,
    (m, s) => _Raw(
      s,
      m.end,
      SmartTokenKind.recurrence,
      recurrence: const Recurrence(RecurrenceKind.weekly),
    ),
  );
  // "every month" / "monthly" → monthly.
  scan(
    _monthlyWord,
    (m, s) => _Raw(
      s,
      m.end,
      SmartTokenKind.recurrence,
      recurrence: Recurrence.monthly,
    ),
  );
  // "every year" / "yearly" / "annually" → yearly.
  scan(
    _yearlyWord,
    (m, s) => _Raw(
      s,
      m.end,
      SmartTokenKind.recurrence,
      recurrence: Recurrence.yearly,
    ),
  );

  // ── Project (first token only) ─────────────────────────────────────────
  final pm = _project.firstMatch(input);
  if (pm != null) {
    raws.add(
      _Raw(ts(pm), pm.end, SmartTokenKind.project, project: pm.group(2)),
    );
  }

  return raws;
}

/// The upcoming [weekday] (1=Mon … 7=Sun) on/after [today]; today when it lands
/// on [today]. With [nextWeek] true, the occurrence one week later.
DateTime _weekdayDate(DateTime today, int weekday, {required bool nextWeek}) {
  final delta = (weekday - today.weekday) % 7; // 0..6, 0 = today
  return today.add(Duration(days: delta + (nextWeek ? 7 : 0)));
}

// ── Priority ─────────────────────────────────────────────────────────────────

const Map<String, String> _priorityByCode = {
  '1': 'urgent',
  '2': 'high',
  '3': 'medium',
  '4': 'low',
};

// `!p1`/`!1` … `!p4`/`!4` — only as a standalone whitespace-delimited token.
final RegExp _priorityCode = RegExp(
  r'(^|\s)!p?([1-4])(?=\s|$)',
  caseSensitive: false,
);

// Bare `p1`/`p2`/`p3`/`p4` (no leading bang), standalone token only.
final RegExp _priorityBare = RegExp(
  r'(^|\s)p([1-4])(?=\s|$)',
  caseSensitive: false,
);

// Bare bangs: `!`=medium, `!!`=high, `!!!`=urgent — standalone token only.
final RegExp _priorityBangs = RegExp(r'(^|\s)(!{1,3})(?=\s|$)');

// ── Project ──────────────────────────────────────────────────────────────────

// `#name` or `/name` at a token boundary. The token-boundary anchor protects
// against `and/or` (slash mid-word) and `6/10` (M/D dates) being misread.
final RegExp _project = RegExp(r'(^|\s)[#/]([A-Za-z0-9_-]+)');

// ── Dates ────────────────────────────────────────────────────────────────────

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

// Times (NOT calendar dates — they only imply "today" when no date is present).
// `9am`, `9:30pm`, `9a`, `12p` — 12-hour clock incl. single-letter am/pm.
final RegExp _clock12 = RegExp(
  r'(^|\s)(\d{1,2})(:\d{2})?\s*(am|pm|a|p)(?=\s|$)',
  caseSensitive: false,
);
final RegExp _clock24 = RegExp(r'(^|\s)([01]?\d|2[0-3]):([0-5]\d)(?=\s|$)');
// `in 2h`, `in 2 hours`, `in 2 hrs` — a duration; resolves to now + N hours.
final RegExp _inNHours = RegExp(
  r'(^|\s)in\s+(\d+)\s*h(?:ours?|rs?)?(?=\s|$)',
  caseSensitive: false,
);
// Time-of-day keywords -> a wall-clock hour. `midnight` precedes `night` so the
// longer token wins the alternation.
final RegExp _timeOfDay = RegExp(
  r'(^|\s)(morning|afternoon|evening|midnight|night|noon)(?=\s|$)',
  caseSensitive: false,
);

const Map<String, int> _timesOfDay = {
  'morning': 9,
  'afternoon': 13,
  'evening': 18,
  'night': 20,
  'noon': 12,
  'midnight': 0,
};

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

/// Build a date, returning null for out-of-range month/day rather than letting
/// [DateTime] silently roll over (e.g. 13/45 -> next February).
DateTime? _safeDate(int year, int month, int day) {
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  final d = DateTime(year, month, day);
  if (d.month != month || d.day != day) return null; // e.g. 2/30
  return d;
}
