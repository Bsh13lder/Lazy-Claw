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
/// (earliest start wins; longest wins on a tie; [Raw.rank] backstops a
/// genuine start+length tie) and a token can only be one kind. The clean
/// title is the original minus the union of accepted spans, with whitespace
/// collapsed.
///
/// It is pure Dart (no I/O, no async) and never throws — unrecognized tokens are
/// simply left in the title.
///
/// The matchers themselves live in one file per family under `smart_add/`
/// (`dates.dart`, `times.dart`, `priority.dart`, `recurrence_patterns.dart`,
/// `project.dart`) plus the shared `smart_add/collector.dart`. They are
/// `part of` this library so they can share [Raw], [_weekdayDate], and
/// [_safeDate] without re-exporting internals.
///
/// `#project`/`/project` recognition itself (the regex + [removeProjectToken])
/// lives one level up, in `smart_add/project_token.dart` — a plain (non-`part
/// of`) file imported (and re-exported) here so the sibling expense parser
/// (`smart_add_expense_parser.dart`) can match against the exact same pattern
/// without forking it.
library;

import 'due_date.dart';
import 'recurrence.dart';
import 'smart_add/project_token.dart';

export 'smart_add/project_token.dart' show removeProjectToken;

part 'smart_add/collector.dart';
part 'smart_add/dates.dart';
part 'smart_add/times.dart';
part 'smart_add/priority.dart';
part 'smart_add/recurrence_patterns.dart';
part 'smart_add/project.dart';

/// What a recognized [SmartToken] represents. Drives the live highlight color.
/// `amount` is only ever produced by the sibling expense parser
/// (`smart_add_expense_parser.dart`) — [parseSmartAdd] never emits it — but
/// the enum is shared so both parsers hand [SmartAddController] the same
/// [SmartToken] type.
enum SmartTokenKind { date, time, priority, project, recurrence, amount }

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
      case SmartTokenKind.amount:
      // parseSmartAdd's own `_collect` never emits `amount` — only the
      // sibling expense parser (`smart_add_expense_parser.dart`) does — but
      // the switch must stay exhaustive so a real future addition to this
      // enum can't silently skip a case here.
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
class Raw {
  final int start;
  final int end;
  final SmartTokenKind kind;

  /// Tiebreak priority for [_resolveOverlaps] when two matches share both
  /// `start` and `length` — see the rank scale documented in
  /// `smart_add/collector.dart`.
  final int rank;

  final DateTime? date; // date kind
  final int? hour; // time kind
  final int? minute; // time kind
  final DateTime? timeDate; // time kind: optional day override (in Nh)
  final String? priority; // priority kind
  final String? project; // project kind
  final Recurrence? recurrence; // recurrence kind
  final DateTime?
  recurrenceDate; // recurrence kind: implied due date ("every monday")
  const Raw(
    this.start,
    this.end,
    this.kind, {
    required this.rank,
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
List<Raw> _resolveOverlaps(List<Raw> raws) {
  final sorted = [...raws]
    ..sort((a, b) {
      if (a.start != b.start) return a.start.compareTo(b.start);
      if (a.length != b.length) return b.length.compareTo(a.length); // longest first on a tie
      return b.rank.compareTo(a.rank); // higher rank wins a genuine start+length tie
    });
  final accepted = <Raw>[];
  var lastEnd = -1;
  for (final r in sorted) {
    if (r.start >= lastEnd) {
      accepted.add(r);
      lastEnd = r.end;
    }
  }
  return accepted;
}

/// Run every matcher family against the original [input] and collect raw
/// hits. Project only ever contributes its FIRST hit (later `#tags` stay in
/// the title).
List<Raw> _collect(String input, DateTime ref, DateTime today) {
  final c = _Collector(input, ref, today);
  _collectDates(c);
  _collectTimes(c);
  _collectPriority(c);
  _collectRecurrence(c);
  _collectProject(c);
  return c.raws;
}

/// The upcoming [weekday] (1=Mon … 7=Sun) on/after [today]; today when it lands
/// on [today]. With [nextWeek] true, the occurrence one week later.
DateTime _weekdayDate(DateTime today, int weekday, {required bool nextWeek}) {
  final delta = (weekday - today.weekday) % 7; // 0..6, 0 = today
  return today.add(Duration(days: delta + (nextWeek ? 7 : 0)));
}

/// Build a date, returning null for out-of-range month/day rather than letting
/// [DateTime] silently roll over (e.g. 13/45 -> next February).
DateTime? _safeDate(int year, int month, int day) {
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  final d = DateTime(year, month, day);
  if (d.month != month || d.day != day) return null; // e.g. 2/30
  return d;
}
