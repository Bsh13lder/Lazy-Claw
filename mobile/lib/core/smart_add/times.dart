part of '../smart_add_parser.dart';

// Times (NOT calendar dates — they only imply "today" when no date is present).
// `9am`, `9:30pm`, `9a`, `12p` — 12-hour clock incl. single-letter am/pm.
// The leading `(?:at\s+)?` (G2 #7) is NON-CAPTURING: a capturing group here
// would shift every payload group index below (h/min/am-pm) and produce a
// silently WRONG time, not a compile error. It exists purely so "at 5pm"
// absorbs the "at" into the token span too, instead of stranding it.
final RegExp _clock12 = RegExp(
  r'(^|\s)(?:at\s+)?(\d{1,2})(:\d{2})?\s*(am|pm|a|p)(?=\s|$)',
  caseSensitive: false,
);
final RegExp _clock24 = RegExp(
  r'(^|\s)(?:at\s+)?([01]?\d|2[0-3]):([0-5]\d)(?=\s|$)',
);
// `in 2h`, `in 2 hours`, `in 2 hrs` — a duration; resolves to now + N hours.
final RegExp _inNHours = RegExp(
  r'(^|\s)in\s+(\d+)\s*h(?:ours?|rs?)?(?=\s|$)',
  caseSensitive: false,
);
// Time-of-day keywords -> a wall-clock hour. `midnight` precedes `night` so the
// longer token wins the alternation. The leading optional cue (G2 #8) is also
// NON-CAPTURING (same reasoning as `_clock12`/`_clock24` above) — it absorbs
// "this"/"tomorrow"/"tmrw"/"tmr"/"tom" directly in front of the keyword so
// e.g. "this morning" doesn't strand "this" in the title. Because it's
// non-capturing we can't read which cue matched off a group, so the callback
// below re-derives it from the raw match text instead (see `_collectTimes`).
final RegExp _timeOfDay = RegExp(
  r'(^|\s)(?:(?:this|tomorrow|tmrw|tmr|tom)\s+)?(morning|afternoon|evening|midnight|night|noon)(?=\s|$)',
  caseSensitive: false,
);
// "tonight" (moved from the date family, G2 #9) -> today at 20:00, reusing
// the exact `timeDate` payload shape `_inNHours` already uses.
final RegExp _tonight = RegExp(r'(^|\s)tonight(?=\s|$)', caseSensitive: false);

const Map<String, int> _timesOfDay = {
  'morning': 9,
  'afternoon': 13,
  'evening': 18,
  'night': 20,
  'noon': 12,
  'midnight': 0,
};

// The tomorrow-ish cues `_timeOfDay` can absorb ahead of the keyword. When
// one of these is what got absorbed, the combined match now fully subsumes
// (and out-ranks, being longer) the separate `_tomorrow` date token on the
// same text — so this token must carry the day forward itself via
// `timeDate`, or "tomorrow morning" would silently resolve to TODAY morning.
// `this` is deliberately excluded: it carries no day of its own.
const Set<String> _timeOfDayTomorrowCues = {'tomorrow', 'tmrw', 'tmr', 'tom'};

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
    return Raw(
      s,
      m.end,
      SmartTokenKind.time,
      rank: _rankTime,
      hour: h,
      minute: min,
    );
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
    final keyword = m.group(2)!;
    final h = _timesOfDay[keyword.toLowerCase()];
    if (h == null) return null;
    // Re-derive the absorbed cue (if any) from the raw text between the
    // token start and the keyword — see the non-capturing-group note above.
    final cue = c.input
        .substring(s, m.end - keyword.length)
        .trim()
        .toLowerCase();
    final timeDate = _timeOfDayTomorrowCues.contains(cue)
        ? c.today.add(const Duration(days: 1))
        : null;
    return Raw(
      s,
      m.end,
      SmartTokenKind.time,
      rank: _rankTime,
      hour: h,
      minute: 0,
      timeDate: timeDate,
    );
  });
  c.scan(
    _tonight,
    (m, s) => Raw(
      s,
      m.end,
      SmartTokenKind.time,
      rank: _rankTime,
      hour: 20,
      minute: 0,
      timeDate: c.today,
    ),
  );
}
