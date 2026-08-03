// Unit tests for the "Reschedule all" batch flow.
//
//   * TasksNotifier.rescheduleMany applies the same target (date-only dueDate +
//     dayed reminderAt) to EVERY listed id, in one cache refresh, and leaves a
//     third (non-listed) task untouched.
//   * isOverdueTask selects only past-due, not-done tasks.
//
// Backed by a real in-memory FFI database so the batch update genuinely
// persists through the DAO + outbox (the same pattern as tasks_provider_test).

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart';
import 'package:lazyclaw_mobile/repositories/tasks_repository.dart';
import 'package:lazyclaw_mobile/screens/tasks_screen.dart' show isOverdueTask;
import 'package:lazyclaw_mobile/sync/task_sync.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

/// Transport that always fails the network — proves the batch reschedule works
/// fully offline (writes land locally + dirty, list reads from cache).
class _OfflineTransport implements TasksTransport {
  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async => throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  ) async => throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> patchJson(
    String path,
    Map<String, dynamic> body,
  ) async => throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> putJson(
    String path,
    Map<String, dynamic> body,
  ) async => throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async =>
      throw ApiError(0, 'offline');
}

int _dbCounter = 0;

Future<TaskDao> _freshDao() async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:reschedmem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return TaskDao(db);
}

Task _byId(TasksNotifier n, String id) =>
    n.state.tasks.firstWhere((t) => t.id == id);

void main() {
  setUpAll(() => sqfliteFfiInit());

  group('TasksNotifier.rescheduleMany', () {
    test(
      'applies the target to every listed id and leaves the rest untouched',
      () async {
        final dao = await _freshDao();
        // Three overdue tasks; we'll reschedule a + b but NOT c.
        await dao.applyLocalCreate('A', id: 'a', dueDate: '2026-01-01');
        await dao.applyLocalCreate('B', id: 'b', dueDate: '2026-01-02');
        await dao.applyLocalCreate('C', id: 'c', dueDate: '2026-01-03');

        final n = TasksNotifier(
          dao,
          TaskSync(dao, TasksRepository(_OfflineTransport())),
        );
        await n.load();

        await n.rescheduleMany(
          ['a', 'b'],
          dueDate: '2026-07-10',
          reminderAt: '2026-07-10T10:00:00',
        );
        // Let the unawaited best-effort sync settle.
        await Future<void>.delayed(const Duration(milliseconds: 20));

        // Both listed ids moved to the new date-only due + dayed reminder.
        for (final id in ['a', 'b']) {
          expect(_byId(n, id).dueDate, '2026-07-10', reason: 'due $id');
          expect(_byId(n, id).reminderAt, '2026-07-10T10:00:00',
              reason: 'reminder $id');
        }
        // The non-listed task keeps its original due + has no reminder.
        expect(_byId(n, 'c').dueDate, '2026-01-03');
        expect(_byId(n, 'c').reminderAt, isNull);
        expect(n.state.error, isNull);
      },
    );

    test('an empty id list is a no-op (no error)', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('Solo', id: 'solo', dueDate: '2026-01-01');
      final n = TasksNotifier(
        dao,
        TaskSync(dao, TasksRepository(_OfflineTransport())),
      );
      await n.load();

      await n.rescheduleMany(
        const [],
        dueDate: '2026-07-10',
        reminderAt: '2026-07-10T10:00:00',
      );
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(_byId(n, 'solo').dueDate, '2026-01-01');
      expect(n.state.error, isNull);
    });
  });

  group('isOverdueTask', () {
    Task make({String? dueDate, String status = 'todo'}) => Task(
      id: 't',
      userId: '',
      title: 'x',
      priority: 'medium',
      status: status,
      owner: 'user',
      dueDate: dueDate,
      nagCount: 0,
      createdAt: '',
    );

    // Pin "now" to 2026-06-26 so the assertions don't drift with the clock.
    final now = DateTime(2026, 6, 26, 9);

    test('past-due, not-done task IS overdue', () {
      expect(isOverdueTask(make(dueDate: '2026-06-25'), now: now), isTrue);
    });

    test('due today is NOT overdue', () {
      expect(isOverdueTask(make(dueDate: '2026-06-26'), now: now), isFalse);
    });

    test('future due is NOT overdue', () {
      expect(isOverdueTask(make(dueDate: '2026-06-27'), now: now), isFalse);
    });

    test('past-due but DONE is NOT overdue', () {
      expect(
        isOverdueTask(make(dueDate: '2026-06-25', status: 'done'), now: now),
        isFalse,
      );
    });

    test('no due date is NOT overdue', () {
      expect(isOverdueTask(make(dueDate: null), now: now), isFalse);
    });

    test('unparseable due date is NOT overdue', () {
      expect(isOverdueTask(make(dueDate: 'someday'), now: now), isFalse);
    });

    test(
      '.toLocal() parity (D1): a UTC-aware due date crossing local midnight '
      'into "today" is NOT overdue',
      () {
        // now = 2026-08-04 local. '2026-08-03T23:30:00Z' is
        // 2026-08-04T01:30:00 in Europe/Madrid (+2h CEST, this
        // worktree/CI's TZ) — LOCAL today, not yesterday. Reading
        // .year/.month/.day off the raw UTC parse instead gives Aug 3,
        // which wrongly marks the task overdue a day early.
        final today = DateTime(2026, 8, 4, 10);
        expect(
          isOverdueTask(make(dueDate: '2026-08-03T23:30:00Z'), now: today),
          isFalse,
        );
      },
    );
  });
}
