part of '../smart_add_parser.dart';

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

/// Every time-of-day matcher, run against the original input. All emit
/// `SmartTokenKind.time` [Raw]s ranked `_rankTime`.
void _collectTimes(_Collector c) {
  final ref = c.ref;

  c.scan(_clock12, (m, s) {
    var h = int.parse(m.group(2)!);
    if (h < 1 || h > 12) return null; // not a valid 12-hour clock
    final min = m.group(3) != null ? int.parse(m.group(3)!.substring(1)) : 0;
    if (min > 59) return null;
    final ap = m.group(4)!.toLowerCase();
    final pm = ap == 'pm' || ap == 'p';
    if (h == 12) h = 0; // 12am -> 0; 12pm -> 0, then +12 below
    if (pm) h += 12;
    return Raw(s, m.end, SmartTokenKind.time, rank: _rankTime, hour: h, minute: min);
  });
  c.scan(
    _clock24,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.time,
      rank: _rankTime,
      hour: int.parse(m.group(2)!),
      minute: int.parse(m.group(3)!),
    ),
  );
  c.scan(_inNHours, (m, s) {
    final target = ref.add(Duration(hours: int.parse(m.group(2)!)));
    return Raw(
      s,
      m.end,
      SmartTokenKind.time,
      rank: _rankTime,
      hour: target.hour,
      minute: target.minute,
      timeDate: DateTime(target.year, target.month, target.day),
    );
  });
  c.scan(_timeOfDay, (m, s) {
    final h = _timesOfDay[m.group(2)!.toLowerCase()];
    if (h == null) return null;
    return Raw(s, m.end, SmartTokenKind.time, rank: _rankTime, hour: h, minute: 0);
  });
}
