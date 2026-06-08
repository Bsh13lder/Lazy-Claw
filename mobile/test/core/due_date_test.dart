// Unit tests for the dual-shape dueDate helpers (date-only vs datetime).

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/due_date.dart';

void main() {
  group('dueDateHasTime', () {
    test('date-only string has no time', () {
      expect(dueDateHasTime('2026-06-08'), isFalse);
    });
    test('datetime string has a time', () {
      expect(dueDateHasTime('2026-06-08T17:00:00'), isTrue);
    });
    test('null has no time', () {
      expect(dueDateHasTime(null), isFalse);
    });
  });

  test('dueDateDayPart drops the time component', () {
    expect(dueDateDayPart('2026-06-08T17:00:00'), '2026-06-08');
    expect(dueDateDayPart('2026-06-08'), '2026-06-08');
  });

  group('dueTimeParts', () {
    test('extracts hour and minute', () {
      expect(dueTimeParts('2026-06-08T17:05:00'), (hour: 17, minute: 5));
    });
    test('null for date-only', () {
      expect(dueTimeParts('2026-06-08'), isNull);
    });
  });

  group('formatClock12', () {
    test('afternoon -> PM', () => expect(formatClock12(17, 0), '5:00 PM'));
    test('morning -> AM', () => expect(formatClock12(9, 30), '9:30 AM'));
    test('midnight -> 12 AM', () => expect(formatClock12(0, 0), '12:00 AM'));
    test('noon -> 12 PM', () => expect(formatClock12(12, 0), '12:00 PM'));
  });

  test('formatDueTimeLabel returns null for date-only', () {
    expect(formatDueTimeLabel('2026-06-08'), isNull);
    expect(formatDueTimeLabel('2026-06-08T15:00:00'), '3:00 PM');
  });

  test('dueDateDisplay appends the time when present', () {
    expect(dueDateDisplay('2026-06-08'), '2026-06-08');
    expect(dueDateDisplay('2026-06-08T17:00:00'), '2026-06-08 · 5:00 PM');
  });

  group('composeDueDate', () {
    test('day only when no time', () {
      expect(composeDueDate(DateTime(2026, 6, 8)), '2026-06-08');
    });
    test('datetime when hour+minute given', () {
      expect(composeDueDate(DateTime(2026, 6, 8), hour: 17, minute: 0),
          '2026-06-08T17:00:00');
    });
    test('zero-pads single-digit fields', () {
      expect(composeDueDate(DateTime(2026, 1, 3), hour: 5, minute: 9),
          '2026-01-03T05:09:00');
    });
  });
}
