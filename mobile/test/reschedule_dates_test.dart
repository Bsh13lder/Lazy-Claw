// Unit tests for the Smart Fast Reschedule pure date math.
//
// Every quick-date function takes `now` explicitly, so these tests pin exact
// calendar days (including the today-is-Saturday and today-is-Monday edges)
// without touching the wall clock.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/reschedule_dates.dart';

void main() {
  // A fixed reference week for clarity:
  //   2026-06-25 is a THURSDAY.
  //   2026-06-26 Fri · 27 Sat · 28 Sun · 29 Mon · 30 Tue.
  group('tomorrow', () {
    test('is the next calendar day, date-only', () {
      final t = tomorrow(DateTime(2026, 6, 25, 14, 32));
      expect(t, DateTime(2026, 6, 26));
    });

    test('rolls over month boundaries', () {
      expect(tomorrow(DateTime(2026, 6, 30, 9)), DateTime(2026, 7, 1));
    });

    test('strips the time component', () {
      final t = tomorrow(DateTime(2026, 6, 25, 23, 59, 59));
      expect(t.hour, 0);
      expect(t.minute, 0);
    });
  });

  group('thisWeekend (coming Saturday)', () {
    test('Thursday -> this Saturday', () {
      // Thu 2026-06-25 -> Sat 2026-06-27.
      expect(thisWeekend(DateTime(2026, 6, 25, 8)), DateTime(2026, 6, 27));
    });

    test('today IS Saturday -> today', () {
      // Sat 2026-06-27 -> itself.
      expect(thisWeekend(DateTime(2026, 6, 27, 8)), DateTime(2026, 6, 27));
    });

    test('Sunday -> next Saturday (6 days out)', () {
      // Sun 2026-06-28 -> Sat 2026-07-04.
      expect(thisWeekend(DateTime(2026, 6, 28, 8)), DateTime(2026, 7, 4));
    });

    test('Friday -> the very next day Saturday', () {
      // Fri 2026-06-26 -> Sat 2026-06-27.
      expect(thisWeekend(DateTime(2026, 6, 26, 8)), DateTime(2026, 6, 27));
    });
  });

  group('nextWeek (coming Monday)', () {
    test('Thursday -> the next Monday', () {
      // Thu 2026-06-25 -> Mon 2026-06-29.
      expect(nextWeek(DateTime(2026, 6, 25, 8)), DateTime(2026, 6, 29));
    });

    test('today IS Monday -> next Monday (7 days out)', () {
      // Mon 2026-06-29 -> Mon 2026-07-06.
      expect(nextWeek(DateTime(2026, 6, 29, 8)), DateTime(2026, 7, 6));
    });

    test('Sunday -> the very next day Monday', () {
      // Sun 2026-06-28 -> Mon 2026-06-29.
      expect(nextWeek(DateTime(2026, 6, 28, 8)), DateTime(2026, 6, 29));
    });
  });

  group('composeAt', () {
    test('defaults to 10:00 — date-only dueDate + day@10:00 reminderAt', () {
      final t = composeAt(DateTime(2026, 6, 26));
      expect(t.dueDate, '2026-06-26');
      expect(t.reminderAt, '2026-06-26T10:00:00');
    });

    test('zero-pads month, day, hour and minute', () {
      final t = composeAt(DateTime(2026, 1, 5), hour: 9, minute: 7);
      expect(t.dueDate, '2026-01-05');
      expect(t.reminderAt, '2026-01-05T09:07:00');
    });

    test('honors an overridden time', () {
      final t = composeAt(DateTime(2026, 12, 31), hour: 18, minute: 30);
      expect(t.dueDate, '2026-12-31');
      expect(t.reminderAt, '2026-12-31T18:30:00');
    });

    test('dueDate is always date-only (never carries a T)', () {
      final t = composeAt(DateTime(2026, 6, 26), hour: 10, minute: 0);
      expect(t.dueDate.contains('T'), isFalse);
      expect(t.dueDate.length, 10);
    });
  });

  group('quick-date + composeAt end-to-end at 10:00', () {
    test('Tomorrow lands on the right day @ 10:00', () {
      final t = composeAt(tomorrow(DateTime(2026, 6, 25, 14)));
      expect(t.dueDate, '2026-06-26');
      expect(t.reminderAt, '2026-06-26T10:00:00');
    });

    test('This weekend (from Thursday) @ 10:00', () {
      final t = composeAt(thisWeekend(DateTime(2026, 6, 25, 14)));
      expect(t.dueDate, '2026-06-27');
      expect(t.reminderAt, '2026-06-27T10:00:00');
    });

    test('Next week (from Thursday) @ 10:00', () {
      final t = composeAt(nextWeek(DateTime(2026, 6, 25, 14)));
      expect(t.dueDate, '2026-06-29');
      expect(t.reminderAt, '2026-06-29T10:00:00');
    });
  });
}
