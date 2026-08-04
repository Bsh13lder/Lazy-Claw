// The ONE timestamp label used for tasks, sub-tasks and comments.
//
// Every case pins `now` explicitly. A wall-clock-dependent expectation here
// would pass today and rot silently (the "8 months ago" case in particular
// flips branch the moment the machine's date moves), which is exactly the
// class of bug this helper exists to prevent.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/relative_time.dart';

/// Local copy of the month table so the absolute-branch assertions compare
/// against a literal expectation rather than re-running the implementation.
const _months = [
  '',
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

void main() {
  // 2026-08-04 12:00 UTC. Every relative case is measured from here.
  final now = DateTime.utc(2026, 8, 4, 12);

  group('formatTimestampLabel — nothing to render', () {
    test('null input returns null (legacy sub-task with no created_at)', () {
      expect(formatTimestampLabel(null, now: now), isNull);
    });

    test('empty and whitespace-only input return null', () {
      expect(formatTimestampLabel('', now: now), isNull);
      expect(formatTimestampLabel('   ', now: now), isNull);
    });

    test('unparseable garbage returns null rather than throwing', () {
      // A hand-edited or half-migrated steps blob must cost one missing date,
      // never the whole row — same tolerance rule as Subtask._coerceTimestamp.
      expect(formatTimestampLabel('yesterday', now: now), isNull);
      expect(formatTimestampLabel('{}', now: now), isNull);
      expect(formatTimestampLabel('null', now: now), isNull);
      expect(formatTimestampLabel('0', now: now), isNull);
    });

    test('an out-of-range ISO string rolls over instead of being rejected — '
        'deliberately NOT stricter than the model layer', () {
      // `DateTime.tryParse` accepts out-of-range components and normalises
      // them (`2026-13-45T99:99:99Z` → 2027-02-18 04:40:39Z). Rejecting that
      // here would make the LABEL stricter than `Subtask._coerceTimestamp`,
      // which stores the value using the very same `tryParse`: the row would
      // round-trip a timestamp the UI then refused to show, which is a worse
      // failure than showing a nonsense-but-honest date. Pinned so nobody
      // "fixes" it into a hand-rolled range validator by accident.
      expect(formatTimestampLabel('2026-13-45T99:99:99Z', now: now), isNotNull);
    });
  });

  group('formatTimestampLabel — recent reads relatively', () {
    test('under a minute is "just now"', () {
      expect(
        formatTimestampLabel('2026-08-04T11:59:31Z', now: now),
        'just now',
      );
    });

    test('minutes', () {
      expect(formatTimestampLabel('2026-08-04T11:55:00Z', now: now), '5m ago');
    });

    test('hours', () {
      expect(formatTimestampLabel('2026-08-04T09:00:00Z', now: now), '3h ago');
    });

    test('days, up to the one-week cutoff', () {
      expect(formatTimestampLabel('2026-08-02T12:00:00Z', now: now), '2d ago');
      expect(
        formatTimestampLabel('2026-07-29T12:00:00Z', now: now),
        '6d ago',
      );
    });

    test('a timestamp slightly in the future reads "just now", not "in 3m"', () {
      // Client and server both stamp these; a few seconds of clock skew must
      // not surface as a negative duration or a nonsense forward label.
      expect(
        formatTimestampLabel('2026-08-04T12:03:00Z', now: now),
        'just now',
      );
    });
  });

  group('formatTimestampLabel — old reads as a real date', () {
    test('exactly a week old crosses over to an absolute date', () {
      final label = formatTimestampLabel('2026-07-28T12:00:00Z', now: now);
      expect(label, isNotNull);
      expect(label, isNot(contains('ago')));
      // Jul 28 12:00 UTC lands on Jul 28 (or Jul 29 at +14) everywhere.
      expect(label, contains('Jul'));
    });

    test('eight months ago is a date, never "241d ago"', () {
      final label = formatTimestampLabel('2025-12-04T12:00:00Z', now: now);
      expect(label, isNot(contains('ago')));
      expect(label, contains('Dec'));
    });

    test('a different year carries the year', () {
      expect(formatTimestampLabel('2025-11-20T10:00:00Z', now: now), contains('2025'));
    });

    test('the current year omits the year (less noise on the common case)', () {
      // Jan 15 10:00 UTC cannot fall back into 2025 in any real zone.
      final label = formatTimestampLabel('2026-01-15T10:00:00Z', now: now);
      expect(label, contains('Jan'));
      expect(label, isNot(contains('2026')));
    });
  });

  group('formatTimestampLabel — UTC in, LOCAL out', () {
    test('the absolute date is the LOCAL calendar day, not the UTC one', () {
      // 23:30 UTC is deliberately close to midnight: on any machine east of
      // UTC this is already the NEXT local day, so an implementation that
      // read the parsed UTC fields directly renders the wrong date here.
      const iso = '2026-03-10T23:30:00Z';
      final local = DateTime.utc(2026, 3, 10, 23, 30).toLocal();
      expect(
        formatTimestampLabel(iso, now: now),
        '${_months[local.month]} ${local.day}',
      );
    });

    test('offsets are compared as INSTANTS, not as naive wall-clock fields', () {
      // Both of these ARE 12:00 UTC. A naive comparison (the drift bug this
      // project has shipped repeatedly) would report them hours out.
      expect(
        formatTimestampLabel('2026-08-04T17:30:00+05:30', now: now),
        'just now',
      );
      expect(
        formatTimestampLabel('2026-08-04T07:00:00-05:00', now: now),
        'just now',
      );
      // …and 30 minutes before that instant is 30 minutes, whatever the offset.
      expect(
        formatTimestampLabel('2026-08-04T06:30:00-05:00', now: now),
        '30m ago',
      );
    });

    test('the `+00:00` and `Z` spellings of one instant agree', () {
      // The server writes `+00:00` (datetime.isoformat), the client writes `Z`
      // (toIso8601String). Both ride in the same steps blob.
      expect(
        formatTimestampLabel('2026-08-04T11:55:00+00:00', now: now),
        formatTimestampLabel('2026-08-04T11:55:00Z', now: now),
      );
    });
  });

  test('`now` defaults to the real clock', () {
    expect(
      formatTimestampLabel(DateTime.now().toUtc().toIso8601String()),
      'just now',
    );
  });
}
