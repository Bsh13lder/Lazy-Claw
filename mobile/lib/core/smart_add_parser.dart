/// On-device, offline, zero-LLM "Todoist-style" smart-add parsing.
///
/// Given a raw task title typed by the user, [parseSmartAdd] pulls out
/// natural-language tokens for due date, priority, and project, strips them
/// from the title, and returns the cleaned title alongside the parsed values.
///
/// It is pure Dart (no I/O, no async) and never throws — unrecognized tokens
/// are simply left in the title.
library;

/// The structured result of parsing a smart-add title.
class ParsedTask {
  /// The title with every recognized token removed and whitespace collapsed.
  final String cleanTitle;

  /// ISO `yyyy-MM-dd`, or null when no date token was recognized.
  final String? dueDate;

  /// One of `low | medium | high | urgent`, or null.
  final String? priority;

  /// Project / category name, or null.
  final String? project;

  const ParsedTask({
    required this.cleanTitle,
    this.dueDate,
    this.priority,
    this.project,
  });
}

/// Parse [input] into a [ParsedTask]. [now] is injectable for deterministic
/// tests; it defaults to [DateTime.now]. Only the date (not the time-of-day) is
/// stored, so a bare time token resolves to today's calendar date.
ParsedTask parseSmartAdd(String input, {DateTime? now}) {
  final ref = now ?? DateTime.now();
  final today = DateTime(ref.year, ref.month, ref.day);

  var working = input;

  final priority = _extractPriority(working);
  working = priority.stripped;

  final date = _extractDate(working, today);
  working = date.stripped;

  final project = _extractProject(working);
  working = project.stripped;

  final cleanTitle = working.replaceAll(_whitespace, ' ').trim();

  return ParsedTask(
    cleanTitle: cleanTitle,
    dueDate: date.value,
    priority: priority.value,
    project: project.value,
  );
}

// ── internals ────────────────────────────────────────────────────────────────

final RegExp _whitespace = RegExp(r'\s+');

/// A parsed field plus the input with its recognized tokens removed.
class _Field {
  final String? value;
  final String stripped;
  const _Field(this.value, this.stripped);
}

// ── Priority ─────────────────────────────────────────────────────────────────

const Map<String, String> _priorityByCode = {
  '1': 'urgent',
  '2': 'high',
  '3': 'medium',
  '4': 'low',
};

// `!p1`/`!1` … `!p4`/`!4` — only as a standalone whitespace-delimited token.
final RegExp _priorityCode =
    RegExp(r'(^|\s)!p?([1-4])(?=\s|$)', caseSensitive: false);

// Bare bangs: `!`=medium, `!!`=high, `!!!`=urgent — standalone token only.
final RegExp _priorityBangs = RegExp(r'(^|\s)(!{1,3})(?=\s|$)');

_Field _extractPriority(String input) {
  String? value;
  var working = input;

  final code = _priorityCode.firstMatch(working);
  if (code != null) value = _priorityByCode[code.group(2)!];
  working = working.replaceAllMapped(_priorityCode, (_) => ' ');

  final bang = _priorityBangs.firstMatch(working);
  if (value == null && bang != null) {
    final n = bang.group(2)!.length;
    value = n == 3 ? 'urgent' : (n == 2 ? 'high' : 'medium');
  }
  working = working.replaceAllMapped(_priorityBangs, (_) => ' ');

  return _Field(value, working);
}

// ── Project ──────────────────────────────────────────────────────────────────

// `#name` or `/name` at a token boundary. The token-boundary anchor protects
// against `and/or` (slash mid-word) and `6/10` (M/D dates) being misread.
final RegExp _project = RegExp(r'(^|\s)[#/]([A-Za-z0-9_-]+)');

_Field _extractProject(String input) {
  final m = _project.firstMatch(input);
  if (m == null) return _Field(null, input);
  // Strip only the first project token (keep its leading whitespace).
  final stripped = input.replaceRange(m.start, m.end, m.group(1) ?? '');
  return _Field(m.group(2), stripped);
}

// ── Dates ────────────────────────────────────────────────────────────────────

const Map<String, int> _weekdays = {
  'mon': 1, 'monday': 1,
  'tue': 2, 'tuesday': 2,
  'wed': 3, 'wednesday': 3,
  'thu': 4, 'thursday': 4,
  'fri': 5, 'friday': 5,
  'sat': 6, 'saturday': 6,
  'sun': 7, 'sunday': 7,
};

final RegExp _isoDate =
    RegExp(r'(^|\s)(\d{4})-(\d{2})-(\d{2})(?=\s|$)');
final RegExp _mdDate = RegExp(r'(^|\s)(\d{1,2})/(\d{1,2})(?=\s|$)');
final RegExp _inNDays =
    RegExp(r'(^|\s)in\s+(\d+)\s+days?(?=\s|$)', caseSensitive: false);
final RegExp _nextWeek = RegExp(r'(^|\s)next\s+week(?=\s|$)', caseSensitive: false);
final RegExp _tomorrow = RegExp(r'(^|\s)tomorrow(?=\s|$)', caseSensitive: false);
final RegExp _todayWord =
    RegExp(r'(^|\s)(today|tonight)(?=\s|$)', caseSensitive: false);
final RegExp _weekdayWord = RegExp(
    r'(^|\s)(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)(?=\s|$)',
    caseSensitive: false);

// Times (NOT calendar dates — they only imply "today" when no date is present).
final RegExp _clock12 = RegExp(
    r'(^|\s)(\d{1,2})(:\d{2})?\s*(am|pm)(?=\s|$)',
    caseSensitive: false);
final RegExp _clock24 = RegExp(r'(^|\s)([01]?\d|2[0-3]):([0-5]\d)(?=\s|$)');

/// A single accepted date/time hit within the working string.
class _DateHit {
  final int start;
  final int end;
  final DateTime date;
  const _DateHit(this.start, this.end, this.date);
}

_Field _extractDate(String input, DateTime today) {
  final calendar = <_DateHit>[];
  final times = <_DateHit>[];

  void scan(RegExp re, List<_DateHit> bucket, DateTime? Function(RegExpMatch) f) {
    for (final m in re.allMatches(input)) {
      final d = f(m);
      if (d != null) bucket.add(_DateHit(m.start, m.end, d));
    }
  }

  scan(_isoDate, calendar, (m) {
    final y = int.parse(m.group(2)!);
    final mo = int.parse(m.group(3)!);
    final da = int.parse(m.group(4)!);
    return _safeDate(y, mo, da);
  });
  scan(_mdDate, calendar, (m) {
    final mo = int.parse(m.group(2)!);
    final da = int.parse(m.group(3)!);
    return _safeDate(today.year, mo, da);
  });
  scan(_inNDays, calendar,
      (m) => today.add(Duration(days: int.parse(m.group(2)!))));
  scan(_nextWeek, calendar, (_) => today.add(const Duration(days: 7)));
  scan(_tomorrow, calendar, (_) => today.add(const Duration(days: 1)));
  scan(_todayWord, calendar, (_) => today);
  scan(_weekdayWord, calendar, (m) {
    final wd = _weekdays[m.group(2)!.toLowerCase()];
    if (wd == null) return null;
    final delta = (wd - today.weekday) % 7; // 0 when it's today
    return today.add(Duration(days: delta));
  });

  scan(_clock12, times, (_) => today);
  scan(_clock24, times, (_) => today);

  String? value;
  if (calendar.isNotEmpty) {
    calendar.sort((a, b) => a.start.compareTo(b.start));
    value = _iso(calendar.first.date);
  } else if (times.isNotEmpty) {
    value = _iso(today);
  }

  // Strip every accepted token (calendar + time), right-to-left so earlier
  // removals don't shift later indices. Each removed span -> a single space.
  final ranges = [...calendar, ...times]
    ..sort((a, b) => b.start.compareTo(a.start));
  var stripped = input;
  for (final r in ranges) {
    stripped = stripped.replaceRange(r.start, r.end, ' ');
  }

  return _Field(value, stripped);
}

/// Build a date, returning null for out-of-range month/day rather than letting
/// [DateTime] silently roll over (e.g. 13/45 -> next February).
DateTime? _safeDate(int year, int month, int day) {
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  final d = DateTime(year, month, day);
  if (d.month != month || d.day != day) return null; // e.g. 2/30
  return d;
}

String _iso(DateTime d) =>
    '${d.year.toString().padLeft(4, '0')}-'
    '${d.month.toString().padLeft(2, '0')}-'
    '${d.day.toString().padLeft(2, '0')}';
