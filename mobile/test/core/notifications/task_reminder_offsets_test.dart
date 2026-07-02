// Unit tests for the MULTI-offset scheduling path of TaskReminderService.
//
// The service depends on a thin [ReminderSink] seam so we can assert exactly
// how many notifications are scheduled/cancelled (one per configured offset)
// without a real plugin / platform channel. Far-future due dates are used so
// the service's internal `DateTime.now()` past-filter never trims them.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/notifications/task_reminder_service.dart';
import 'package:lazyclaw_mobile/models/task.dart';

Task _task({
  String id = 't1',
  String? dueDate,
  String? reminderAt,
  String status = 'todo',
}) =>
    Task(
      id: id,
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

class _FakeSink implements ReminderSink {
  final List<int> cancelled = <int>[];
  final List<({int id, DateTime fire})> scheduled = <({int id, DateTime fire})>[];
  List<int> pending = <int>[];

  @override
  Future<void> cancel(int id) async => cancelled.add(id);

  @override
  Future<void> schedule({
    required int id,
    required String title,
    required String body,
    required DateTime fire,
    required String payload,
  }) async =>
      scheduled.add((id: id, fire: fire));

  @override
  Future<List<int>> pendingIds() async => List<int>.of(pending);
}

void main() {
  group('reminderBaseTime (offset anchor)', () {
    test('timed due → the due instant', () {
      expect(
        reminderBaseTime(_task(dueDate: '2099-01-01T17:00:00')),
        DateTime(2099, 1, 1, 17, 0, 0),
      );
    });

    test('date-only due → that date at the configured default time-of-day', () {
      expect(
        reminderBaseTime(
          _task(dueDate: '2099-01-02'),
          defaultReminderHour: 8,
          defaultReminderMinute: 15,
        ),
        DateTime(2099, 1, 2, 8, 15, 0),
      );
    });

    test('a timed due is preferred over reminderAt as the anchor', () {
      // Offsets are measured from the EVENT (due), not the legacy single lead.
      expect(
        reminderBaseTime(_task(
          dueDate: '2099-01-01T17:00:00',
          reminderAt: '2099-01-01T16:30:00',
        )),
        DateTime(2099, 1, 1, 17, 0, 0),
      );
    });

    test('no due but an explicit reminderAt → the reminderAt instant', () {
      expect(
        reminderBaseTime(_task(reminderAt: '2099-01-01T20:15:00')),
        DateTime(2099, 1, 1, 20, 15, 0),
      );
    });

    test('nothing to anchor → null', () {
      expect(reminderBaseTime(_task()), isNull);
    });
  });

  group('notificationIdForOffset', () {
    test('index 0 coincides with the base task id (fallback shares one id)', () {
      expect(notificationIdForOffset('t1', 0), notificationIdForTask('t1'));
    });

    test('distinct ids per index', () {
      final ids = {
        for (var i = 0; i < kMaxOffsetReminders; i++)
          notificationIdForOffset('t1', i),
      };
      expect(ids, hasLength(kMaxOffsetReminders));
    });

    test('always a positive 31-bit int', () {
      for (var i = 0; i < kMaxOffsetReminders; i++) {
        final v = notificationIdForOffset('some-uuid-id', i);
        expect(v, greaterThanOrEqualTo(0));
        expect(v, lessThanOrEqualTo(0x7FFFFFFF));
      }
    });
  });

  group('scheduleForTask (multi-offset)', () {
    test('N offsets → N schedule calls at the right instants, distinct ids',
        () async {
      final sink = _FakeSink();
      final svc = TaskReminderService(sink, offsets: const ['0m', '-30m', '-1d']);

      await svc.scheduleForTask(_task(dueDate: '2099-01-01T17:00:00'));

      expect(sink.scheduled, hasLength(3));
      expect(
        sink.scheduled.map((s) => s.fire).toSet(),
        {
          DateTime(2098, 12, 31, 17, 0, 0), // -1d
          DateTime(2099, 1, 1, 16, 30, 0), // -30m
          DateTime(2099, 1, 1, 17, 0, 0), // 0m
        },
      );
      // Distinct notification ids so the OS keeps them as separate alarms.
      expect(sink.scheduled.map((s) => s.id).toSet(), hasLength(3));
    });

    test('cancels the whole reserved id range before scheduling', () async {
      final sink = _FakeSink();
      final svc = TaskReminderService(sink, offsets: const ['0m', '-30m']);

      await svc.scheduleForTask(_task(dueDate: '2099-01-01T17:00:00'));

      // The reserved range (kMaxOffsetReminders ids) is swept up-front.
      expect(sink.cancelled, hasLength(kMaxOffsetReminders));
      expect(sink.cancelled, contains(notificationIdForTask('t1')));
      expect(sink.cancelled, contains(notificationIdForOffset('t1', 1)));
    });

    test('empty offsets → exactly one reminder (legacy single behaviour)',
        () async {
      final sink = _FakeSink();
      final svc = TaskReminderService(sink, offsets: const []);

      await svc.scheduleForTask(_task(
        dueDate: '2099-01-01T17:00:00',
        reminderAt: '2099-01-01T16:30:00',
      ));

      expect(sink.scheduled, hasLength(1));
      // Falls back to reminderFireTime (respects the pre-baked reminderAt lead).
      expect(sink.scheduled.single.fire, DateTime(2099, 1, 1, 16, 30, 0));
      expect(sink.scheduled.single.id, notificationIdForTask('t1'));
    });

    test('a done task schedules nothing (only cancels)', () async {
      final sink = _FakeSink();
      final svc = TaskReminderService(sink, offsets: const ['0m', '-30m']);

      await svc.scheduleForTask(
        _task(dueDate: '2099-01-01T17:00:00', status: 'done'),
      );

      expect(sink.scheduled, isEmpty);
      expect(sink.cancelled, hasLength(kMaxOffsetReminders));
    });

    test('setting offsets live takes effect on the next schedule', () async {
      final sink = _FakeSink();
      final svc = TaskReminderService(sink, offsets: const ['0m']);
      svc.offsets = const ['0m', '-30m', '-1h'];

      await svc.scheduleForTask(_task(dueDate: '2099-01-01T17:00:00'));

      expect(sink.scheduled, hasLength(3));
    });
  });

  group('cancelForTask', () {
    test('cancels the whole reserved id range', () async {
      final sink = _FakeSink();
      final svc = TaskReminderService(sink, offsets: const ['0m']);

      await svc.cancelForTask('t1');

      expect(sink.cancelled, hasLength(kMaxOffsetReminders));
      expect(sink.cancelled, contains(notificationIdForTask('t1')));
      expect(sink.cancelled, contains(notificationIdForOffset('t1', 2)));
    });
  });

  group('syncAll (multi-offset reconciliation)', () {
    test('cancels a stale offset id then schedules the desired set', () async {
      final sink = _FakeSink();
      final svc = TaskReminderService(sink, offsets: const ['0m', '-30m']);

      final task = _task(dueDate: '2099-01-01T17:00:00');
      // Pending includes the 2 desired ids PLUS a stale reserved-slot id.
      final staleId = notificationIdForOffset(task.id, 5);
      sink.pending = [
        notificationIdForOffset(task.id, 0),
        notificationIdForOffset(task.id, 1),
        staleId,
      ];

      await svc.syncAll([task]);

      // The stale reserved-slot id (belongs to the task, not desired) is culled.
      expect(sink.cancelled, contains(staleId));
      // The desired 2 offsets are (re)scheduled.
      expect(sink.scheduled, hasLength(2));
    });

    test('a completed task has its whole reserved range culled from pending',
        () async {
      final sink = _FakeSink();
      final svc = TaskReminderService(sink, offsets: const ['0m', '-30m']);

      final done = _task(dueDate: '2099-01-01T17:00:00', status: 'done');
      sink.pending = [
        notificationIdForOffset(done.id, 0),
        notificationIdForOffset(done.id, 1),
      ];

      await svc.syncAll([done]);

      expect(sink.cancelled, contains(notificationIdForOffset(done.id, 0)));
      expect(sink.cancelled, contains(notificationIdForOffset(done.id, 1)));
      expect(sink.scheduled, isEmpty);
    });

    test('does NOT cancel a foreign pending id (non-task / other schedule)',
        () async {
      final sink = _FakeSink();
      final svc = TaskReminderService(sink, offsets: const ['0m']);

      final task = _task(dueDate: '2099-01-01T17:00:00');
      const foreignId = 424242; // e.g. the "Schedule test reminder" id
      sink.pending = [foreignId];

      await svc.syncAll([task]);

      expect(sink.cancelled, isNot(contains(foreignId)));
    });
  });
}
