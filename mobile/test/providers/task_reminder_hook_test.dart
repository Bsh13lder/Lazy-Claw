// Verifies TasksNotifier hooks the reminder scheduler at the right lifecycle
// points: add/update → scheduleForTask, complete/delete → cancelForTask, and a
// load() (re)syncs the whole set. Uses the abstract [TaskReminderScheduler]
// seam (a recording fake) so no plugin / platform channel is involved.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/core/notifications/task_reminder_service.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart';
import 'package:lazyclaw_mobile/repositories/tasks_repository.dart';
import 'package:lazyclaw_mobile/sync/task_sync.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

class _OfflineTransport implements TasksTransport {
  @override
  Future<Map<String, dynamic>> getJson(String path,
          {Map<String, dynamic>? queryParams}) async =>
      throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> postJson(
          String path, Map<String, dynamic> body) async =>
      throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> patchJson(
          String path, Map<String, dynamic> body) async =>
      throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> putJson(
          String path, Map<String, dynamic> body) async =>
      throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async =>
      throw ApiError(0, 'offline');
}

class _NoopSync extends TaskSync {
  _NoopSync(super.dao, super.repo);
  @override
  Future<SyncResult> sync({bool retryRejected = false}) async => const SyncResult();
}

/// Sync that simulates a server PULL landing a task the client never created —
/// exactly what happens when the backend respawns the next occurrence of a
/// recurring task on completion (new row, brand-new server-minted id).
class _PullingSync extends TaskSync {
  _PullingSync(this._dao, TasksRepository repo) : super(_dao, repo);

  final TaskDao _dao;
  static const pulledId = 'server-spawned-next-occurrence';

  @override
  Future<SyncResult> sync({bool retryRejected = false}) async {
    await _dao.upsertFromServer(
      const Task(
        id: pulledId,
        userId: 'u1',
        title: 'Water the plants',
        priority: 'medium',
        status: 'pending',
        owner: 'user',
        dueDate: '2099-09-09',
        reminderAt: '2099-09-09T08:00:00',
        recurring: '0 8 * * *',
        nagCount: 0,
        createdAt: '2026-07-26T00:00:00Z',
      ),
      serverUpdatedAt: '2026-07-26T00:00:00Z',
    );
    return const SyncResult();
  }
}

class _FakeReminders implements TaskReminderScheduler {
  final List<Task> scheduled = [];
  final List<String> cancelled = [];
  int syncAllCalls = 0;

  @override
  Future<void> scheduleForTask(Task task) async => scheduled.add(task);
  @override
  Future<void> cancelForTask(String taskId) async => cancelled.add(taskId);
  @override
  Future<void> syncAll(List<Task> tasks) async => syncAllCalls++;
}

int _dbCounter = 0;

Future<TaskDao> _freshDao() async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:remindmem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return TaskDao(db);
}

TasksNotifier _notifier(TaskDao dao, _FakeReminders fake) => TasksNotifier(
      dao,
      _NoopSync(dao, TasksRepository(_OfflineTransport())),
      fake,
    );

Future<void> _settle() =>
    Future<void>.delayed(const Duration(milliseconds: 30));

void main() {
  setUpAll(() => sqfliteFfiInit());

  group('TasksNotifier reminder hooks', () {
    test('addTask schedules the freshly created task', () async {
      final dao = await _freshDao();
      final fake = _FakeReminders();
      final n = _notifier(dao, fake);

      await n.addTask('Pay rent', dueDate: '2099-01-01T17:00:00');
      await _settle();

      expect(fake.scheduled, hasLength(1));
      expect(fake.scheduled.single.title, 'Pay rent');
      expect(fake.scheduled.single.dueDate, '2099-01-01T17:00:00');
      expect(fake.cancelled, isEmpty);
    });

    test('updateTask reschedules the updated task', () async {
      final dao = await _freshDao();
      final fake = _FakeReminders();
      final n = _notifier(dao, fake);

      final created =
          await dao.applyLocalCreate('Call vet', dueDate: '2099-02-02');
      await n.updateTask(created.id, dueDate: '2099-02-02T09:30:00');
      await _settle();

      expect(fake.scheduled, isNotEmpty);
      expect(fake.scheduled.last.id, created.id);
      expect(fake.scheduled.last.dueDate, '2099-02-02T09:30:00');
    });

    test('completeTask cancels the reminder by id', () async {
      final dao = await _freshDao();
      final fake = _FakeReminders();
      final n = _notifier(dao, fake);

      final created =
          await dao.applyLocalCreate('Submit form', dueDate: '2099-03-03T08:00:00');
      await n.completeTask(created.id);
      await _settle();

      expect(fake.cancelled, contains(created.id));
    });

    test('deleteTask cancels the reminder by id', () async {
      final dao = await _freshDao();
      final fake = _FakeReminders();
      final n = _notifier(dao, fake);

      final created =
          await dao.applyLocalCreate('Stale task', dueDate: '2099-04-04T08:00:00');
      await n.deleteTask(created.id);
      await _settle();

      expect(fake.cancelled, contains(created.id));
    });

    test('load() syncs the whole reminder set from the cache', () async {
      final dao = await _freshDao();
      final fake = _FakeReminders();
      final n = _notifier(dao, fake);

      await dao.applyLocalCreate('A', dueDate: '2099-05-05T08:00:00');
      await n.load();
      await _settle();

      expect(fake.syncAllCalls, greaterThanOrEqualTo(1));
    });

    test('addTask plumbs an explicit reminderAt through to the schedule',
        () async {
      final dao = await _freshDao();
      final fake = _FakeReminders();
      final n = _notifier(dao, fake);

      await n.addTask('Pay rent',
          dueDate: '2099-01-01T17:00:00', reminderAt: '2099-01-01T16:30:00');
      await _settle();

      expect(fake.scheduled.single.reminderAt, '2099-01-01T16:30:00');
    });

    test('addTask normalises an empty reminderAt to null (no reminder)',
        () async {
      final dao = await _freshDao();
      final fake = _FakeReminders();
      final n = _notifier(dao, fake);

      await n.addTask('Pay rent',
          dueDate: '2099-01-01T17:00:00', reminderAt: '');
      await _settle();

      expect(fake.scheduled.single.reminderAt, isNull);
    });

    test('updateTask plumbs reminderAt through to the reschedule', () async {
      final dao = await _freshDao();
      final fake = _FakeReminders();
      final n = _notifier(dao, fake);

      final created =
          await dao.applyLocalCreate('Call vet', dueDate: '2099-02-02T09:30:00');
      await n.updateTask(created.id, reminderAt: '2099-02-02T09:00:00');
      await _settle();

      expect(fake.scheduled.last.reminderAt, '2099-02-02T09:00:00');
    });

    // Regression (2026-07-26): completing a RECURRING task cancelled the local
    // notification for the completed occurrence but nothing ever scheduled one
    // for the NEXT occurrence. The next occurrence is minted SERVER-side (new
    // id) and arrives via the /changes pull inside _syncThenRefresh, and only
    // load() reconciled the reminder set — so the respawned task stayed
    // notification-less until the next cold start. Every server-originated task
    // (recurring respawn, a task added on web/Telegram, a reminder edited
    // elsewhere) hit the same hole.
    test('a pull that lands a server-spawned task reconciles the reminder set',
        () async {
      final dao = await _freshDao();
      final fake = _FakeReminders();
      final n = TasksNotifier(
        dao,
        _PullingSync(dao, TasksRepository(_OfflineTransport())),
        fake,
      );

      final created = await dao.applyLocalCreate(
        'Water the plants',
        dueDate: '2099-09-08T08:00:00',
      );
      await n.completeTask(created.id);
      await _settle();

      // The pulled next occurrence really did land in the cache.
      expect(
        n.state.tasks.map((t) => t.id),
        contains(_PullingSync.pulledId),
        reason: 'the simulated pull should have landed the next occurrence',
      );
      // …and the scheduler was reconciled against it, so it can fire.
      expect(
        fake.syncAllCalls,
        greaterThanOrEqualTo(1),
        reason: 'a sync that pulls new server rows must reconcile reminders, '
            'or the respawned recurring occurrence never notifies',
      );
    });

    test('a null scheduler is a no-op (legacy 2-arg construction still works)',
        () async {
      final dao = await _freshDao();
      final n = TasksNotifier(
        dao,
        _NoopSync(dao, TasksRepository(_OfflineTransport())),
      );

      // Must not throw despite no scheduler wired.
      await n.addTask('No reminders', dueDate: '2099-06-06T08:00:00');
      await n.completeTask('whatever');
      await _settle();
      expect(n.state.error, isNull);
    });
  });
}
