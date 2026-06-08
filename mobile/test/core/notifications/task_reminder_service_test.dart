// Unit tests for the pure scheduling-decision helpers in
// core/notifications/task_reminder_service.dart.
//
// These are plain-Dart functions (no plugin, no platform channels), so they're
// tested directly with constructed Task fixtures and a fixed `now`.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/notifications/task_reminder_service.dart';
import 'package:lazyclaw_mobile/models/task.dart';

Task _task({String? dueDate, String? reminderAt, String status = 'todo'}) =>
    Task(
      id: 't1',
      userId: 'u1',
      title: 'Pay rent',
      priority: 'medium',
      status: status,
      owner: 'user',
      nagCount: 0,
      createdAt: '2026-06-01T00:00:00',
      dueDate: dueDate,
      reminderAt: reminderAt,
    );

void main() {
  // Fixed reference instant for every "is it in the future?" check.
  final now = DateTime(2026, 6, 7, 12, 0, 0);

  group('reminderFireTime', () {
    test('future timed due (no reminderAt) → that datetime', () {
      final t = _task(dueDate: '2026-06-07T17:00:00');
      expect(reminderFireTime(t, now: now), DateTime(2026, 6, 7, 17, 0, 0));
    });

    test('past timed due (no reminderAt) → null', () {
      final t = _task(dueDate: '2026-06-07T09:00:00');
      expect(reminderFireTime(t, now: now), isNull);
    });

    test('a timed due exactly at now → null (the moment already passed)', () {
      final t = _task(dueDate: '2026-06-07T12:00:00');
      expect(reminderFireTime(t, now: now), isNull);
    });

    test('future date-only due → that date at the default 09:00', () {
      final t = _task(dueDate: '2026-06-08');
      expect(reminderFireTime(t, now: now), DateTime(2026, 6, 8, 9, 0, 0));
    });

    test('future date-only due → the configured default time when passed', () {
      final t = _task(dueDate: '2026-06-08');
      expect(
        reminderFireTime(
          t,
          now: now,
          defaultReminderHour: 14,
          defaultReminderMinute: 30,
        ),
        DateTime(2026, 6, 8, 14, 30, 0),
      );
    });

    test('a date-only due TODAY whose default time is still ahead → fires', () {
      // now = 2026-06-07 12:00; default 18:00 is later today, so it schedules.
      final t = _task(dueDate: '2026-06-07');
      expect(
        reminderFireTime(t, now: now, defaultReminderHour: 18),
        DateTime(2026, 6, 7, 18, 0, 0),
      );
    });

    test('a date-only due TODAY whose default time already passed → null', () {
      // now = 2026-06-07 12:00; default 09:00 already passed today → no fire.
      final t = _task(dueDate: '2026-06-07');
      expect(reminderFireTime(t, now: now), isNull);
    });

    test('a past date-only due → null', () {
      final t = _task(dueDate: '2026-06-01');
      expect(reminderFireTime(t, now: now), isNull);
    });

    test('reminderAt wins over a date-only due (explicit beats the fallback)',
        () {
      final t = _task(dueDate: '2026-06-08', reminderAt: '2026-06-08T07:15:00');
      expect(reminderFireTime(t, now: now), DateTime(2026, 6, 8, 7, 15, 0));
    });

    test('no due and no reminderAt → null', () {
      expect(reminderFireTime(_task(), now: now), isNull);
    });

    test('reminderAt wins over a future timed due', () {
      final t = _task(
        dueDate: '2026-06-07T17:00:00',
        reminderAt: '2026-06-07T16:30:00',
      );
      expect(reminderFireTime(t, now: now), DateTime(2026, 6, 7, 16, 30, 0));
    });

    test('reminderAt is authoritative: past reminderAt → null (no fallback to '
        'a future due)', () {
      final t = _task(
        dueDate: '2026-06-07T17:00:00', // future
        reminderAt: '2026-06-07T08:00:00', // past
      );
      expect(reminderFireTime(t, now: now), isNull);
    });

    test('future reminderAt with no due → reminderAt', () {
      final t = _task(reminderAt: '2026-06-07T20:15:00');
      expect(reminderFireTime(t, now: now), DateTime(2026, 6, 7, 20, 15, 0));
    });

    test('empty-string reminderAt falls through to the due time', () {
      final t = _task(dueDate: '2026-06-07T17:00:00', reminderAt: '');
      expect(reminderFireTime(t, now: now), DateTime(2026, 6, 7, 17, 0, 0));
    });

    test('unparseable due → null', () {
      final t = _task(dueDate: 'not-a-date-with-T');
      expect(reminderFireTime(t, now: now), isNull);
    });

    test('defaults now to DateTime.now() — a far-future due still schedules',
        () {
      final t = _task(dueDate: '2099-01-01T10:00:00');
      expect(reminderFireTime(t), DateTime(2099, 1, 1, 10, 0, 0));
    });
  });

  group('notificationIdForTask', () {
    test('is deterministic for the same id', () {
      expect(notificationIdForTask('task-1'), notificationIdForTask('task-1'));
    });

    test('differs for different ids', () {
      expect(
        notificationIdForTask('task-1'),
        isNot(notificationIdForTask('task-2')),
      );
    });

    test('is always a positive 31-bit int', () {
      for (final id in ['', 'a', 'task-1', 'long-uuid-style-id-xyz-123']) {
        final v = notificationIdForTask(id);
        expect(v, greaterThanOrEqualTo(0));
        expect(v, lessThanOrEqualTo(0x7FFFFFFF));
      }
    });

    test('matches pinned FNV-1a/31 reference values (stability guard)', () {
      // Computed independently; pins the algorithm so a refactor that changes
      // the mapping (and would orphan already-scheduled reminders) is caught.
      expect(notificationIdForTask('task-1'), 137425990);
      expect(notificationIdForTask('abc'), 440920331);
      expect(
        notificationIdForTask('550e8400-e29b-41d4-a716-446655440000'),
        144586024,
      );
      expect(notificationIdForTask(''), 18652613);
    });
  });
}
