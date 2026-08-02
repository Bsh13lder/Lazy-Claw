import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/models/comment.dart';
import 'package:lazyclaw_mobile/repositories/tasks_repository.dart';
import 'package:lazyclaw_mobile/sync/task_sync.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

// ── Programmable fake transport (mirrors task_sync_test.dart's harness) ─────

class _Call {
  final String method;
  final String path;
  final Map<String, dynamic>? body;
  final Map<String, dynamic>? query;
  _Call(this.method, this.path, {this.body, this.query});
}

class _FakeTransport implements TasksTransport {
  final List<_Call> calls = [];
  Map<String, dynamic> changesResponse;

  final Set<String> failNetworkOnPaths;
  final Map<String, int> failServerOnPaths;
  final Map<String, DioException Function()> dioErrorOnPaths;

  _FakeTransport({
    Map<String, dynamic>? changesResponse,
    Set<String>? failNetworkOnPaths,
    Map<String, int>? failServerOnPaths,
    Map<String, DioException Function()>? dioErrorOnPaths,
  })  : changesResponse =
            changesResponse ?? {'tasks': [], 'deleted': [], 'now': ''},
        failNetworkOnPaths = failNetworkOnPaths ?? const {},
        failServerOnPaths = failServerOnPaths ?? const {},
        dioErrorOnPaths = dioErrorOnPaths ?? const {};

  void _maybeFail(String path) {
    for (final entry in dioErrorOnPaths.entries) {
      if (path.contains(entry.key)) throw entry.value();
    }
    for (final frag in failNetworkOnPaths) {
      if (path.contains(frag)) throw ApiError(0, 'Network error');
    }
    for (final entry in failServerOnPaths.entries) {
      if (path.contains(entry.key)) throw ApiError(entry.value, 'Server error');
    }
  }

  @override
  Future<Map<String, dynamic>> getJson(String path,
      {Map<String, dynamic>? queryParams}) async {
    calls.add(_Call('GET', path, query: queryParams));
    if (path.contains('/changes')) return changesResponse;
    return {'tasks': []};
  }

  @override
  Future<Map<String, dynamic>> postJson(
      String path, Map<String, dynamic> body) async {
    _maybeFail(path);
    calls.add(_Call('POST', path, body: body));
    return {'status': 'ok'};
  }

  @override
  Future<Map<String, dynamic>> patchJson(
      String path, Map<String, dynamic> body) async {
    _maybeFail(path);
    calls.add(_Call('PATCH', path, body: body));
    return {'status': 'ok'};
  }

  @override
  Future<Map<String, dynamic>> putJson(
      String path, Map<String, dynamic> body) async {
    _maybeFail(path);
    calls.add(_Call('PUT', path, body: body));
    return {'status': 'ok'};
  }

  @override
  Future<Map<String, dynamic>> deleteJson(String path) async {
    _maybeFail(path);
    calls.add(_Call('DELETE', path));
    return {'status': 'deleted'};
  }
}

// ── Harness ──────────────────────────────────────────────────────────────────

int _dbCounter = 0;

Future<TaskDao> _freshDao({String Function()? now}) async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:synccommentsmem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return TaskDao(db, now: now);
}

/// A production-shaped server-error DioException: a real HTTP response with a
/// status code, carrying the `ApiError` in `.error` exactly like the app's
/// `_ErrorInterceptor` rethrows it.
DioException _serverDio(int status) {
  final req = RequestOptions(path: '/api/tasks');
  return DioException(
    requestOptions: req,
    type: DioExceptionType.badResponse,
    response: Response(requestOptions: req, statusCode: status),
    error: ApiError(status, 'Server $status'),
  );
}

void main() {
  setUpAll(() => sqfliteFfiInit());

  group('TaskSync.push — comments', () {
    test('comment_add op pushes POST body {id, text, subtask_id}', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('Task one', id: 't1');
      // Drain the create first so only the comment_add op is queued.
      await TaskSync(dao, TasksRepository(_FakeTransport())).push();

      const comment = TaskComment(
        id: 'c-t1',
        ts: '2026-08-02T10:00:00Z',
        author: 'user',
        text: 'hi',
      );
      await dao.applyLocalAddComment('t1', comment);

      final transport = _FakeTransport();
      final sync = TaskSync(dao, TasksRepository(transport));
      final result = await sync.push();

      expect(result.pushed, 1);
      expect(await dao.readOutbox(), isEmpty);

      final postCall = transport.calls.firstWhere((c) => c.method == 'POST');
      expect(postCall.path, '/api/tasks/t1/comments');
      expect(postCall.body, {'id': 'c-t1', 'text': 'hi', 'subtask_id': null});
    });

    test('comment_delete 404 is idempotent success', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('Task two', id: 't2');
      await TaskSync(dao, TasksRepository(_FakeTransport())).push();

      await dao.applyLocalDeleteComment('t2', 'c-missing');

      final transport = _FakeTransport(
        dioErrorOnPaths: {
          '/api/tasks/t2/comments/c-missing': () => _serverDio(404),
        },
      );
      final sync = TaskSync(dao, TasksRepository(transport));
      final result = await sync.push();

      expect(result.pushInterrupted, isFalse);
      expect(result.pushed, 1); // 404-on-commentDelete counted as success
      expect(await dao.readOutbox(), isEmpty);
    });

    test("comment_add on a 404'd task drains without retry", () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('Task three', id: 't3');
      await TaskSync(dao, TasksRepository(_FakeTransport())).push();

      const comment = TaskComment(
        id: 'c-t3',
        ts: '2026-08-02T10:00:00Z',
        author: 'user',
        text: 'hi',
      );
      await dao.applyLocalAddComment('t3', comment);

      final transport = _FakeTransport(
        dioErrorOnPaths: {
          '/api/tasks/t3/comments': () => _serverDio(404),
        },
      );
      final sync = TaskSync(dao, TasksRepository(transport));
      final result = await sync.push();

      // NOT treated as idempotent success — commentAdd stays on the
      // definitive-4xx drain path (the task is gone; next pull tombstones it).
      expect(result.pushInterrupted, isFalse);
      expect(result.pushed, 0);
      expect(await dao.readOutbox(), isEmpty);
    });
  });
}
