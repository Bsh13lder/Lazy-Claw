import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/models/comment.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart';
import 'package:lazyclaw_mobile/repositories/tasks_repository.dart';
import 'package:lazyclaw_mobile/sync/task_sync.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

/// Transport that always fails the network — proves the provider works fully
/// offline (writes land locally + dirty, list reads from cache).
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

    test(
      'addTask persists notes + subtasks and queues them on create',
      () async {
        final dao = await _freshDao();
        final sync = TaskSync(dao, TasksRepository(_OfflineTransport()));
        final n = TasksNotifier(dao, sync);

        const stepsJson = '[{"id":"s1","title":"Step one","done":false}]';
        await n.addTask(
          'Plan trip',
          description: 'Book flights and hotel',
          steps: stepsJson,
        );
        await Future<void>.delayed(const Duration(milliseconds: 20));

        final created = n.state.tasks.firstWhere((t) => t.title == 'Plan trip');
        // Both fields land in the cache (read back via the DAO).
        final stored = await dao.getById(created.id);
        expect(stored!.description, 'Book flights and hotel');
        expect(stored.steps, stepsJson);
        expect(stored.subtasks, hasLength(1));

        // The create op carries both for later server replay.
        final outbox = await dao.readOutbox();
        final createItem = outbox.firstWhere((o) => o.op == OutboxOp.create);
        expect(createItem.payload['description'], 'Book flights and hotel');
        expect(createItem.payload['steps'], stepsJson);
      },
    );

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

    test(
      'deleteTask drops the task from state SYNCHRONOUSLY (Dismissible-safe)',
      () async {
        final dao = await _freshDao();
        final sync = TaskSync(dao, TasksRepository(_OfflineTransport()));
        final n = TasksNotifier(dao, sync);

        await n.addTask('Doomed');
        await Future<void>.delayed(const Duration(milliseconds: 20));
        final id = n.state.tasks.first.id;

        // Fire the delete but DON'T await. A row's Dismissible.onDismissed needs
        // the task gone from state on the very next build, else Flutter throws
        // "A dismissed Dismissible widget is still part of the tree" → the Tasks
        // screen blacks out. So the removal must happen BEFORE the first await.
        final pending = n.deleteTask(id);
        expect(
          n.state.tasks.any((t) => t.id == id),
          isFalse,
          reason: 'must be removed from state synchronously, before any await',
        );
        await pending;
        expect(n.state.tasks, isEmpty);
      },
    );

    test('load reads from cache instantly even with the server down', () async {
      final dao = await _freshDao();
      // Seed a server-synced task directly into the cache.
      await dao.applyLocalCreate('Pre-existing', id: 'pre1');
      final sync = TaskSync(dao, TasksRepository(_OfflineTransport()));
      final n = TasksNotifier(dao, sync);

      await n.load();
      expect(n.state.tasks.map((t) => t.title), contains('Pre-existing'));
      expect(n.state.isLoading, isFalse);
    });

    test('outbox accumulates the offline mutations for later replay', () async {
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
      expect(
        outbox.map((o) => o.op),
        containsAll([OutboxOp.create, OutboxOp.complete]),
      );
    });
  });

  group('TasksNotifier.addComment', () {
    test('appends optimistically + queues comment_add while offline', () async {
      final dao = await _freshDao();
      final sync = TaskSync(dao, TasksRepository(_OfflineTransport()));
      final n = TasksNotifier(dao, sync);

      await n.addTask('Has a thread');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      final id = n.state.tasks.first.id;

      final created = await n.addComment(id, 'hello');
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(created, isNotNull);
      expect(
        n.state.tasks
            .firstWhere((t) => t.id == id)
            .taskComments
            .map((c) => c.text),
        ['hello'],
      );
      final outbox = await dao.readOutbox();
      expect(outbox.map((o) => o.op), contains(OutboxOp.commentAdd));
    });

    // Minor 3 regression: applyLocalAddComment returns null when the task
    // isn't in the cache (nothing was enqueued) — addComment used to ignore
    // that and hand back the minted comment anyway, so a caller (e.g. the
    // subtask comment sheet's optimistic local list) would append a phantom
    // that was never actually persisted.
    test(
      'returns null (mints nothing) when the task is not in the cache',
      () async {
        final dao = await _freshDao();
        final sync = TaskSync(dao, TasksRepository(_OfflineTransport()));
        final n = TasksNotifier(dao, sync);

        final result = await n.addComment('ghost-task', 'orphaned');

        expect(result, isNull);
        final outbox = await dao.readOutbox();
        expect(outbox.map((o) => o.op), isNot(contains(OutboxOp.commentAdd)));
      },
    );

    // Important 2 (defense in depth): the composer already clamps input to
    // kMaxCommentChars, but addComment mirrors the server's own limit so an
    // over-length comment can never be optimistically appended + synced only
    // to be rejected (422) and silently drained later.
    test('rejects text over kMaxCommentChars', () async {
      final dao = await _freshDao();
      final sync = TaskSync(dao, TasksRepository(_OfflineTransport()));
      final n = TasksNotifier(dao, sync);

      await n.addTask('Has a thread');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      final id = n.state.tasks.first.id;

      final tooLong = 'a' * (kMaxCommentChars + 1);
      final result = await n.addComment(id, tooLong);

      expect(result, isNull);
      expect(n.state.tasks.firstWhere((t) => t.id == id).taskComments, isEmpty);
    });
  });
}
