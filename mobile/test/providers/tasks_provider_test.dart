import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart';
import 'package:lazyclaw_mobile/repositories/tasks_repository.dart';
import 'package:lazyclaw_mobile/sync/task_sync.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

/// Transport that always fails the network — proves the provider works fully
/// offline (writes land locally + dirty, list reads from cache).
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

int _dbCounter = 0;

Future<TaskDao> _freshDao() async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:provmem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return TaskDao(db);
}

void main() {
  setUpAll(() => sqfliteFfiInit());

  group('TasksNotifier offline-first', () {
    test('addTask writes to cache + marks dirty while offline', () async {
      final dao = await _freshDao();
      final sync = TaskSync(dao, TasksRepository(_OfflineTransport()));
      final n = TasksNotifier(dao, sync);

      await n.addTask('Buy milk');
      // Let the fire-and-forget sync settle.
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(n.state.tasks.map((t) => t.title), contains('Buy milk'));
      final created = n.state.tasks.firstWhere((t) => t.title == 'Buy milk');
      expect(n.state.dirtyIds, contains(created.id));
    });

    test('completeTask flips status locally + stays dirty offline', () async {
      final dao = await _freshDao();
      final sync = TaskSync(dao, TasksRepository(_OfflineTransport()));
      final n = TasksNotifier(dao, sync);

      await n.addTask('Finish me');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      final id = n.state.tasks.first.id;

      await n.completeTask(id);
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(n.state.tasks.firstWhere((t) => t.id == id).status, 'done');
      expect(n.state.dirtyIds, contains(id));
    });

    test('deleteTask removes from the visible list offline', () async {
      final dao = await _freshDao();
      final sync = TaskSync(dao, TasksRepository(_OfflineTransport()));
      final n = TasksNotifier(dao, sync);

      await n.addTask('Remove me');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      final id = n.state.tasks.first.id;

      await n.deleteTask(id);
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(n.state.tasks.map((t) => t.id), isNot(contains(id)));
    });

    test('load reads from cache instantly even with the server down',
        () async {
      final dao = await _freshDao();
      // Seed a server-synced task directly into the cache.
      await dao.applyLocalCreate('Pre-existing', id: 'pre1');
      final sync = TaskSync(dao, TasksRepository(_OfflineTransport()));
      final n = TasksNotifier(dao, sync);

      await n.load();
      expect(n.state.tasks.map((t) => t.title), contains('Pre-existing'));
      expect(n.state.isLoading, isFalse);
    });

    test('outbox accumulates the offline mutations for later replay',
        () async {
      final dao = await _freshDao();
      final sync = TaskSync(dao, TasksRepository(_OfflineTransport()));
      final n = TasksNotifier(dao, sync);

      await n.addTask('A');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      final id = n.state.tasks.first.id;
      await n.completeTask(id);
      await Future<void>.delayed(const Duration(milliseconds: 20));

      // create + complete queued (none drained — server is offline).
      final outbox = await dao.readOutbox();
      expect(outbox.map((o) => o.op),
          containsAll([OutboxOp.create, OutboxOp.complete]));
    });
  });
}
