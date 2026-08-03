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
    test('naive datetime is read as-is (already local)', () {
      // A no-zone string is local wall-clock; `.toLocal()` must leave it put.
      expect(dueTimeParts('2026-06-08T12:00:00'), (hour: 12, minute: 0));
    });
    test('UTC-aware reminder renders in local time (tz-agnostic round-trip)', () {
      // The server stores reminders UTC-aware; the phone must show the user's
      // LOCAL wall-clock. Build the input by round-tripping a known local
      // instant so this holds on any machine zone (in Madrid noon → 10:00Z).
      final local = DateTime(2026, 6, 8, 12, 0);
      final utcAware = local.toUtc().toIso8601String();
      expect(dueTimeParts(utcAware), (hour: 12, minute: 0));
    });
    test('Z-suffixed UTC reminder also converts to local', () {
      final local = DateTime(2026, 6, 8, 8, 30);
      expect(dueTimeParts(local.toUtc().toIso8601String()), (hour: 8, minute: 30));
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

  group('localDueDay (D1 — the shared .toLocal() day-derivation)', () {
    // Shared by task_calendar_utils.dart's expandRecurringForRange AND
    // tasks_screen.dart's _isOverdueOn/_groupTasks — a single tested
    // implementation instead of three copies of the same
    // DateTime.parse(...).toLocal() dance.
    test('a UTC-aware +02:00 due date resolves to the same LOCAL day, not '
        'the UTC day', () {
      // 2026-08-04T00:00:00+02:00 is 2026-08-03T22:00:00Z. Reading the day
      // off the raw (UTC) parse gives Aug 3 — one day early. `.toLocal()`
      // must resolve it back to Aug 4 on this worktree/CI's Europe/Madrid
      // (+2h CEST) clock. Derive the expected day the same way `.toLocal()`
      // would, so the assertion is a real behavioral check on any machine
      // TZ, not a tautology.
      final expectedLocal =
          DateTime.parse('2026-08-04T00:00:00+02:00').toLocal();
      final expected =
          DateTime(expectedLocal.year, expectedLocal.month, expectedLocal.day);
      expect(localDueDay('2026-08-04T00:00:00+02:00'), expected);
    });

    test('pinned to Europe/Madrid: resolves to Aug 4, not Aug 3', () {
      final localOffsetHours = DateTime.now().timeZoneOffset.inHours;
      if (localOffsetHours != 2 && localOffsetHours != 1) {
        return; // Not Europe/Madrid (CEST +2 / CET +1) — skip the pin.
      }
      expect(localDueDay('2026-08-04T00:00:00+02:00'), DateTime(2026, 8, 4));
    });

    test('a naive/local datetime is left as-is (no-op .toLocal())', () {
      expect(localDueDay('2026-06-08T23:30:00'), DateTime(2026, 6, 8));
    });

    test('a date-only string resolves to that day', () {
      expect(localDueDay('2026-06-08'), DateTime(2026, 6, 8));
    });

    test('null is null', () {
      expect(localDueDay(null), isNull);
    });

    test('empty string is null', () {
      expect(localDueDay(''), isNull);
    });

    test('unparseable string is null (never throws)', () {
      expect(localDueDay('not-a-date'), isNull);
    });
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
