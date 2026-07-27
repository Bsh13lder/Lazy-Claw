// Regression tests for the four reminder-scheduling defects found by the
// recurring-task notification audit (2026-07-27). The user's symptom was
// "notifications on recurring tasks are wrong / missing":
//
//   D1  a respawned occurrence carries a DATE-ONLY due_date + a real
//       reminder_at, and the offset anchor ignored the reminder_at → advance
//       reminders fired around 09:00 while the card showed 8:00 PM.
//   D2  the default offset set (['-2h','-1h']) has no at-time entry → the phone
//       went SILENT at the actual reminder instant.
//   D3  the shade "Done" action cancelled only the tapped alarm → the sibling
//       offset alarms still fired for an already-completed task.
//   D4  the headless (WorkManager) sync pulled the respawned occurrence but
//       never re-armed its alarms → a phone that is never cold-started got no
//       recurring notifications at all.
//
// Far-future dates are used throughout so the service's internal
// `DateTime.now()` past-filter never trims a planned fire.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/notifications/task_reminder_service.dart';
import 'package:lazyclaw_mobile/core/reminder_offset.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/notifications/notification_actions.dart';
import 'package:lazyclaw_mobile/sync/background_sync.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

Task _task({
  String id = 't1',
  String? dueDate,
  String? reminderAt,
  String status = 'todo',
  String? recurring,
}) =>
    Task(
      id: id,
      userId: 'u1',
      title: 'Take medication',
      priority: 'medium',
      status: status,
      owner: 'user',
      nagCount: 0,
      createdAt: '2026-06-01T00:00:00',
      dueDate: dueDate,
      reminderAt: reminderAt,
      recurring: recurring,
    );

class _FakeSink implements ReminderSink {
  final List<int> cancelled = <int>[];
  final List<({int id, DateTime fire})> scheduled =
      <({int id, DateTime fire})>[];
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

/// Records what the headless reconcile handed to the scheduler.
class _FakeScheduler implements TaskReminderScheduler {
  final List<List<Task>> syncedBatches = <List<Task>>[];
  final List<String> cancelled = <String>[];
  final List<Task> individually = <Task>[];

  @override
  Future<void> syncAll(List<Task> tasks) async =>
      syncedBatches.add(List<Task>.of(tasks));

  @override
  Future<void> cancelForTask(String taskId) async => cancelled.add(taskId);

  @override
  Future<void> scheduleForTask(Task task) async => individually.add(task);
}

int _dbCounter = 0;

/// Real in-memory SQLite (ffi) with the production schema, so the "Done"-action
/// path is exercised against the actual engine rather than a hand-rolled fake.
Future<TaskDao> _freshDao() async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:remschedmemdb${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return TaskDao(db);
}

void main() {
  setUpAll(sqfliteFfiInit);

  // ── D1 ─────────────────────────────────────────────────────────────────────
  group('D1 reminderBaseTime — explicit reminderAt beats a date-only due', () {
    test('date-only due + reminderAt → the reminderAt instant', () {
      // The exact shape the backend writes on every recurring respawn.
      expect(
        reminderBaseTime(
          _task(
            dueDate: '2099-01-02',
            reminderAt: '2099-01-02T20:00:00',
            recurring: 'daily',
          ),
        ),
        DateTime(2099, 1, 2, 20, 0, 0),
      );
    });

    test('date-only due with NO reminderAt still anchors at the default '
        'time-of-day', () {
      // Deliberate earlier fix — must not regress.
      expect(
        reminderBaseTime(
          _task(dueDate: '2099-01-02'),
          defaultReminderHour: 8,
          defaultReminderMinute: 15,
        ),
        DateTime(2099, 1, 2, 8, 15, 0),
      );
    });

    test('a TIMED due still wins over reminderAt (offsets lead the event)', () {
      expect(
        reminderBaseTime(_task(
          dueDate: '2099-01-01T17:00:00',
          reminderAt: '2099-01-01T16:30:00',
        )),
        DateTime(2099, 1, 1, 17, 0, 0),
      );
    });

    test('no due at all → the reminderAt instant', () {
      expect(
        reminderBaseTime(_task(reminderAt: '2099-01-01T20:15:00')),
        DateTime(2099, 1, 1, 20, 15, 0),
      );
    });

    test('nothing to anchor → null', () {
      expect(reminderBaseTime(_task()), isNull);
    });

    test('offsets on a respawned 8 PM occurrence fire at 6 PM / 7 PM, '
        'never around 09:00', () async {
      final sink = _FakeSink();
      final svc = TaskReminderService(sink, offsets: const ['-2h', '-1h']);

      await svc.scheduleForTask(_task(
        dueDate: '2099-01-02',
        reminderAt: '2099-01-02T20:00:00',
        recurring: 'daily',
      ));

      final fires = sink.scheduled.map((s) => s.fire).toSet();
      expect(fires, contains(DateTime(2099, 1, 2, 18, 0, 0))); // -2h
      expect(fires, contains(DateTime(2099, 1, 2, 19, 0, 0))); // -1h
      // The 09:00-anchored fires (07:00 / 08:00) must NOT be scheduled.
      expect(fires, isNot(contains(DateTime(2099, 1, 2, 7, 0, 0))));
      expect(fires, isNot(contains(DateTime(2099, 1, 2, 8, 0, 0))));
    });
  });

  // ── D2 ─────────────────────────────────────────────────────────────────────
  group('D2 the at-time reminder comes from the DEFAULTS, not a forced append',
      () {
    test('the shipped default set fires advance AND at the due instant',
        () async {
      final sink = _FakeSink();
      // Exactly what an un-customised user has.
      final svc = TaskReminderService(sink, offsets: kDefaultReminderOffsets);

      await svc.scheduleForTask(_task(dueDate: '2099-01-01T08:00:00'));

      final fires = sink.scheduled.map((s) => s.fire).toSet();
      expect(fires, contains(DateTime(2099, 1, 1, 6, 0, 0))); // -2h
      expect(fires, contains(DateTime(2099, 1, 1, 7, 0, 0))); // -1h
      expect(
        fires,
        contains(DateTime(2099, 1, 1, 8, 0, 0)),
        reason: 'the phone must not go silent at the actual due instant',
      );
      expect(sink.scheduled, hasLength(3));
    });

    // Regression: the at-time fire was force-appended to whatever the user had
    // selected, which made Settings → Notifications → "At time" (a real,
    // toggleable chip, kReminderOffsetOptions[0]) a NO-OP — deselecting it
    // changed nothing on the phone and the UI silently lied. The out-of-box
    // "goes silent at the due instant" problem belongs in the DEFAULT set, not
    // in an override that outranks the user.
    test('deselecting "At time" is respected — no forced at-time fire',
        () async {
      final sink = _FakeSink();
      // A user who explicitly kept only advance reminders.
      final svc = TaskReminderService(sink, offsets: const ['-2h', '-1h']);

      await svc.scheduleForTask(_task(dueDate: '2099-01-01T08:00:00'));

      final fires = sink.scheduled.map((s) => s.fire).toSet();
      expect(fires, contains(DateTime(2099, 1, 1, 6, 0, 0))); // -2h
      expect(fires, contains(DateTime(2099, 1, 1, 7, 0, 0))); // -1h
      expect(
        fires,
        isNot(contains(DateTime(2099, 1, 1, 8, 0, 0))),
        reason: 'the user deselected "At time"; forcing it back makes the '
            'Settings chip a lie',
      );
      expect(sink.scheduled, hasLength(2));
    });

    test('the shipped DEFAULT set includes at-time, so a fresh install is not '
        'silent at the due instant', () async {
      final sink = _FakeSink();
      // Exactly what an un-customised user has.
      final svc = TaskReminderService(sink, offsets: kDefaultReminderOffsets);

      await svc.scheduleForTask(_task(dueDate: '2099-01-01T08:00:00'));

      expect(
        sink.scheduled.map((s) => s.fire),
        contains(DateTime(2099, 1, 1, 8, 0, 0)),
        reason: 'out of the box the phone must not go silent at the due instant',
      );
    });

    test('is NOT double-scheduled when the user already selected "At time"',
        () async {
      final sink = _FakeSink();
      final svc = TaskReminderService(sink, offsets: const ['0m', '-30m']);

      await svc.scheduleForTask(_task(dueDate: '2099-01-01T17:00:00'));

      expect(sink.scheduled, hasLength(2));
      expect(
        sink.scheduled.map((s) => s.fire).toList(),
        [DateTime(2099, 1, 1, 16, 30, 0), DateTime(2099, 1, 1, 17, 0, 0)],
      );
      // Ids stay unique per (task, slot) — a collision would make the OS replace
      // one alarm with the other.
      expect(sink.scheduled.map((s) => s.id).toSet(), hasLength(2));
    });

    test('ids stay unique across every configured offset', () async {
      final sink = _FakeSink();
      final svc = TaskReminderService(
        sink,
        offsets: const ['0m', '-1d', '-2h', '-1h'],
      );

      await svc.scheduleForTask(_task(dueDate: '2099-01-01T08:00:00'));

      expect(sink.scheduled, hasLength(4));
      expect(sink.scheduled.map((s) => s.id).toSet(), hasLength(4));
      // Every id must live inside the task's reserved (and therefore swept)
      // range, else cancel/reschedule leaks an alarm.
      final reserved = {
        for (var i = 0; i < kMaxOffsetReminders; i++)
          notificationIdForOffset('t1', i),
      };
      expect(sink.scheduled.every((s) => reserved.contains(s.id)), isTrue);
    });

    test('a reminder-only task (no due) fires AT its reminderAt', () async {
      final sink = _FakeSink();
      final svc = TaskReminderService(sink, offsets: const ['0m', '-1h']);

      await svc.scheduleForTask(_task(reminderAt: '2099-01-01T20:00:00'));

      final fires = sink.scheduled.map((s) => s.fire).toSet();
      expect(fires, contains(DateTime(2099, 1, 1, 19, 0, 0))); // -1h
      expect(fires, contains(DateTime(2099, 1, 1, 20, 0, 0))); // at time
    });

    test('empty offsets still schedule EXACTLY one reminder (legacy path)',
        () async {
      final sink = _FakeSink();
      final svc = TaskReminderService(sink, offsets: const []);

      await svc.scheduleForTask(_task(
        dueDate: '2099-01-01T17:00:00',
        reminderAt: '2099-01-01T16:30:00',
      ));

      expect(sink.scheduled, hasLength(1));
      expect(sink.scheduled.single.fire, DateTime(2099, 1, 1, 16, 30, 0));
    });

    test('an at-time fire already in the past is not scheduled', () async {
      final sink = _FakeSink();
      final svc = TaskReminderService(sink, offsets: const ['0m', '-1h']);

      // Due 30 min from now: the -1h advance already elapsed, the at-time has
      // not — so exactly one alarm, at the due instant.
      final due = DateTime.now().add(const Duration(minutes: 30));
      String p(int n) => n.toString().padLeft(2, '0');
      await svc.scheduleForTask(_task(
        dueDate: '${due.year}-${p(due.month)}-${p(due.day)}'
            'T${p(due.hour)}:${p(due.minute)}:00',
      ));

      expect(sink.scheduled, hasLength(1));
      expect(sink.scheduled.single.fire.hour, due.hour);
      expect(sink.scheduled.single.fire.minute, due.minute);
    });
  });

  // ── D3 ─────────────────────────────────────────────────────────────────────
  group('D3 the shade "Done" action cancels ALL of the task\'s alarms', () {
    test('completing from the shade sweeps the whole reserved id range',
        () async {
      final dao = await _freshDao();
      final task = await dao.applyLocalCreate('Water the plants');
      final sink = _FakeSink();

      final done = await completeTaskInDao(dao, 'task:${task.id}', sink: sink);

      expect(done, isTrue);
      // Not just the tapped notification: EVERY slot the scheduler could have
      // used, else the -1h sibling still buzzes for a completed task.
      expect(sink.cancelled, hasLength(kMaxOffsetReminders));
      expect(sink.cancelled.toSet(), reservedReminderIds(task.id).toSet());
    });

    test('a payload for a non-existent task cancels nothing', () async {
      final dao = await _freshDao();
      final sink = _FakeSink();

      final done = await completeTaskInDao(dao, 'task:ghost-id', sink: sink);

      expect(done, isFalse);
      expect(sink.cancelled, isEmpty);
    });

    test('a non-task payload is ignored entirely', () async {
      final dao = await _freshDao();
      final task = await dao.applyLocalCreate('Stay open');
      final sink = _FakeSink();

      final done = await completeTaskInDao(dao, 'chat', sink: sink);

      expect(done, isFalse);
      expect(sink.cancelled, isEmpty);
      expect((await dao.getById(task.id))!.status, 'todo');
    });

    test('the completion still lands in the DB + outbox (unchanged mutation)',
        () async {
      final dao = await _freshDao();
      final task = await dao.applyLocalCreate('Pay rent');
      for (final item in await dao.readOutbox()) {
        await dao.deleteOutboxItem(item.seq);
      }

      await completeTaskInDao(dao, 'task:${task.id}', sink: _FakeSink());

      expect((await dao.getById(task.id))!.status, 'done');
      final outbox = await dao.readOutbox();
      expect(outbox, hasLength(1));
      expect(outbox.first.op, OutboxOp.complete);
    });

    test('fires onTaskCompletedFromNotification so an open list can refresh',
        () async {
      final dao = await _freshDao();
      final task = await dao.applyLocalCreate('Refresh me');
      final seen = <String>[];
      onTaskCompletedFromNotification = seen.add;
      addTearDown(() => onTaskCompletedFromNotification = null);

      await completeTaskInDao(dao, 'task:${task.id}', sink: _FakeSink());

      expect(seen, [task.id]);
    });
  });

  // ── D4 ─────────────────────────────────────────────────────────────────────
  group('D4 the headless sync re-arms reminders for what it pulled', () {
    test('hands the whole freshly-synced cache to the scheduler', () async {
      final dao = await _freshDao();
      // The shape a recurring respawn arrives in: date-only due + real
      // reminder_at. Before the fix nothing armed this occurrence's alarms
      // unless the user cold-started the app.
      final respawned = await dao.applyLocalCreate(
        'Take medication',
        dueDate: '2099-01-02',
        reminderAt: '2099-01-02T20:00:00',
        recurring: 'daily',
      );
      final other = await dao.applyLocalCreate('No reminder here');
      final scheduler = _FakeScheduler();

      await reconcileRemindersHeadless(dao, scheduler: scheduler);

      expect(scheduler.syncedBatches, hasLength(1));
      expect(
        scheduler.syncedBatches.single.map((t) => t.id).toSet(),
        {respawned.id, other.id},
      );
    });

    test('an empty cache still reconciles (sweeps stale alarms)', () async {
      final dao = await _freshDao();
      final scheduler = _FakeScheduler();

      await reconcileRemindersHeadless(dao, scheduler: scheduler);

      expect(scheduler.syncedBatches, hasLength(1));
      expect(scheduler.syncedBatches.single, isEmpty);
    });

    test('a throwing scheduler is swallowed (never aborts the sync pass)',
        () async {
      final dao = await _freshDao();
      await expectLater(
        reconcileRemindersHeadless(dao, scheduler: _ThrowingScheduler()),
        completes,
      );
    });

    test('end-to-end: the reconciled respawn arms 6/7/8 PM alarms', () async {
      // Same wiring, but with the REAL service behind a recording sink — proves
      // the headless path produces actual alarms at the D1/D2-correct instants.
      final dao = await _freshDao();
      await dao.applyLocalCreate(
        'Take medication',
        dueDate: '2099-01-02',
        reminderAt: '2099-01-02T20:00:00',
        recurring: 'daily',
      );
      final sink = _FakeSink();

      await reconcileRemindersHeadless(
        dao,
        scheduler: TaskReminderService(sink, offsets: kDefaultReminderOffsets),
      );

      expect(
        sink.scheduled.map((s) => s.fire).toSet(),
        {
          DateTime(2099, 1, 2, 18, 0, 0),
          DateTime(2099, 1, 2, 19, 0, 0),
          DateTime(2099, 1, 2, 20, 0, 0),
        },
      );
    });
  });
}

/// Scheduler whose reconcile blows up — the headless pass must absorb it.
class _ThrowingScheduler implements TaskReminderScheduler {
  @override
  Future<void> syncAll(List<Task> tasks) => throw Exception('plugin missing');
  @override
  Future<void> cancelForTask(String taskId) => throw Exception('plugin missing');
  @override
  Future<void> scheduleForTask(Task task) => throw Exception('plugin missing');
}
