// Unit tests for TasksNotifier.updateTask.
//
// A recording TaskDao subclass captures the exact named args forwarded to
// applyLocalUpdate (the notifier is the seam under test, not the DB), proving:
//   * updateTask forwards the supplied fields to applyLocalUpdate,
//   * it refreshes the visible list afterwards (list() is re-read),
//   * a DAO throw is surfaced as state.error instead of escaping.
//
// The recording DAO is backed by a throwaway in-memory FFI database purely to
// satisfy TaskDao's non-null Database constructor arg; every overridden method
// short-circuits before touching it.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/models/subtask.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart';
import 'package:lazyclaw_mobile/repositories/tasks_repository.dart';
import 'package:lazyclaw_mobile/sync/task_sync.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

/// Transport that always fails — keeps the fire-and-forget sync fully offline.
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
  Future<Map<String, dynamic>> deleteJson(String path) async =>
      throw ApiError(0, 'offline');
}

/// Sync that never touches the network — the notifier's `unawaited` best-effort
/// sync becomes a deterministic no-op so the test only observes updateTask.
class _NoopSync extends TaskSync {
  _NoopSync(super.dao, super.repo);
  @override
  Future<SyncResult> sync() async => const SyncResult();
}

/// TaskDao that records applyLocalUpdate calls and serves canned reads, never
/// hitting the real DB. [throwOnUpdate] exercises the error path.
class _RecordingDao extends TaskDao {
  _RecordingDao(super.db);

  bool throwOnUpdate = false;
  final List<Map<String, dynamic>> updateCalls = [];
  int listCalls = 0;

  @override
  Future<Task?> applyLocalUpdate(
    String id, {
    String? title,
    String? description,
    String? category,
    String? priority,
    String? status,
    String? dueDate,
    String? reminderAt,
    String? steps,
  }) async {
    updateCalls.add({
      'id': id,
      'title': title,
      'description': description,
      'category': category,
      'priority': priority,
      'dueDate': dueDate,
      'steps': steps,
    });
    if (throwOnUpdate) throw StateError('db boom');
    return null;
  }

  @override
  Future<List<Task>> list() async {
    listCalls++;
    return const [];
  }

  @override
  Future<Set<String>> dirtyIds() async => const {};
}

int _dbCounter = 0;

Future<_RecordingDao> _freshDao() async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:updmem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return _RecordingDao(db);
}

void main() {
  setUpAll(() => sqfliteFfiInit());

  group('TasksNotifier.updateTask', () {
    test('forwards the supplied fields to applyLocalUpdate and refreshes',
        () async {
      final dao = await _freshDao();
      final n = TasksNotifier(dao, _NoopSync(dao, TasksRepository(_OfflineTransport())));

      await n.updateTask(
        'task-1',
        title: 'new title',
        description: 'new notes',
        priority: 'high',
        dueDate: '2026-06-09',
      );
      // Let the unawaited best-effort sync settle.
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(dao.updateCalls, hasLength(1));
      final call = dao.updateCalls.single;
      expect(call['id'], 'task-1');
      expect(call['title'], 'new title');
      expect(call['description'], 'new notes');
      expect(call['priority'], 'high');
      expect(call['dueDate'], '2026-06-09');
      // The list was re-read at least once (refresh ran).
      expect(dao.listCalls, greaterThanOrEqualTo(1));
      expect(n.state.error, isNull);
    });

    test('only forwards the fields that were passed', () async {
      final dao = await _freshDao();
      final n = TasksNotifier(dao, _NoopSync(dao, TasksRepository(_OfflineTransport())));

      await n.updateTask('task-2', title: 'just the title');
      await Future<void>.delayed(const Duration(milliseconds: 20));

      final call = dao.updateCalls.single;
      expect(call['title'], 'just the title');
      expect(call['description'], isNull);
      expect(call['priority'], isNull);
      expect(call['dueDate'], isNull);
    });

    test('forwards serialized steps to applyLocalUpdate', () async {
      final dao = await _freshDao();
      final n = TasksNotifier(dao, _NoopSync(dao, TasksRepository(_OfflineTransport())));

      await n.updateTask(
        'task-steps',
        steps: '[{"id":"s1","title":"One","done":false}]',
      );
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(dao.updateCalls.single['steps'],
          '[{"id":"s1","title":"One","done":false}]');
    });

    test('setSubtasks serialises a typed list and persists via the DAO',
        () async {
      final dao = await _freshDao();
      final n = TasksNotifier(dao, _NoopSync(dao, TasksRepository(_OfflineTransport())));

      await n.setSubtasks('task-typed', const [
        Subtask(id: 's1', title: 'Alpha', done: true),
        Subtask(id: 's2', title: 'Beta', done: false),
      ]);
      await Future<void>.delayed(const Duration(milliseconds: 20));

      final steps = dao.updateCalls.single['steps'] as String;
      // The canonical {id,title,done} shape lands in the DAO.
      expect(parseSubtasks(steps), const [
        Subtask(id: 's1', title: 'Alpha', done: true),
        Subtask(id: 's2', title: 'Beta', done: false),
      ]);
    });

    test('setSubtasks with an empty list clears the steps column', () async {
      final dao = await _freshDao();
      final n = TasksNotifier(dao, _NoopSync(dao, TasksRepository(_OfflineTransport())));

      await n.setSubtasks('task-clear', const []);
      await Future<void>.delayed(const Duration(milliseconds: 20));

      // Empty string (not null) so the DAO patch includes + clears the column.
      expect(dao.updateCalls.single['steps'], '');
    });

    test('sets state.error when the DAO throws', () async {
      final dao = await _freshDao()
        ..throwOnUpdate = true;
      final n = TasksNotifier(dao, _NoopSync(dao, TasksRepository(_OfflineTransport())));

      await n.updateTask('task-3', title: 'boom');
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(n.state.error, isNotNull);
      expect(n.state.error, contains('db boom'));
    });
  });
}
