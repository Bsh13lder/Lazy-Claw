// Pure unit tests for the detail sheet's picker seeds + ISO day helper.
// (The picker widgets themselves are exercised through the sheet's own
// widget tests; only the "which day does it open on" rules live here.)

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_detail_pickers.dart';

void main() {
  final now = DateTime(2026, 8, 3);

  group('isoDay', () {
    test('zero-pads month and day', () {
      expect(isoDay(DateTime(2026, 1, 5)), '2026-01-05');
    });

    test('drops any time-of-day', () {
      expect(isoDay(DateTime(2026, 12, 31, 23, 59)), '2026-12-31');
    });
  });

  group('dueDayPickerSeed', () {
    test('defaults to tomorrow with nothing stored', () {
      expect(dueDayPickerSeed(null, now: now), DateTime(2026, 8, 4));
    });

    test('opens on the stored day when it is today or later', () {
      expect(dueDayPickerSeed('2026-08-03', now: now), DateTime(2026, 8, 3));
      expect(dueDayPickerSeed('2026-09-01', now: now), DateTime(2026, 9, 1));
    });

    test(
      'IGNORES a past day — re-opening an overdue task on its old date makes '
      '"pick a new date" start from the wrong month',
      () {
        expect(dueDayPickerSeed('2026-07-01', now: now), DateTime(2026, 8, 4));
      },
    );

    test('falls back rather than throwing on an unparseable value', () {
      expect(dueDayPickerSeed('not-a-date', now: now), DateTime(2026, 8, 4));
    });
  });

  group('recurUntilPickerSeed', () {
    test('defaults to 30 days out', () {
      expect(recurUntilPickerSeed(null, now: now), DateTime(2026, 9, 2));
    });

    test('a future end date wins', () {
      expect(
        recurUntilPickerSeed('2027-01-15', now: now),
        DateTime(2027, 1, 15),
      );
    });

    test('a past end date falls back', () {
      expect(
        recurUntilPickerSeed('2020-01-01', now: now),
        DateTime(2026, 9, 2),
      );
    });
  });
}
