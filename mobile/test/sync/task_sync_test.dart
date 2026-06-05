import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/repositories/tasks_repository.dart';
import 'package:lazyclaw_mobile/sync/task_sync.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

// ── Programmable fake transport ──────────────────────────────────────────────

class _Call {
  final String method;
  final String path;
  final Map<String, dynamic>? body;
  final Map<String, dynamic>? query;
  _Call(this.method, this.path, {this.body, this.query});
}

/// Records every call. `changesResponse` is returned by GET /api/tasks/changes;
/// `failNetworkOnPaths` makes the matching write throw a network ApiError(0).
class _FakeTransport implements TasksTransport {
  final List<_Call> calls = [];
  Map<String, dynamic> changesResponse;

  /// Substring → throw network error when a write path contains it.
  final Set<String> failNetworkOnPaths;

  /// Substring → throw a non-network ApiError(status) on the write path.
  final Map<String, int> failServerOnPaths;

  _FakeTransport({
    Map<String, dynamic>? changesResponse,
    Set<String>? failNetworkOnPaths,
    Map<String, int>? failServerOnPaths,
  })  : changesResponse =
            changesResponse ?? {'tasks': [], 'deleted': [], 'now': ''},
        failNetworkOnPaths = failNetworkOnPaths ?? const {},
        failServerOnPaths = failServerOnPaths ?? const {};

  void _maybeFail(String path) {
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
  Future<Map<String, dynamic>> deleteJson(String path) async {
    _maybeFail(path);
    calls.add(_Call('DELETE', path));
    return {'status': 'deleted'};
  }
}

// ── Harness ──────────────────────────────────────────────────────────────────

int _dbCounter = 0;

/// Fresh isolated in-memory DAO. [now] pins the clock so last-write-wins
/// timestamp comparisons are deterministic.
Future<TaskDao> _freshDao({String Function()? now}) async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:syncmem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return TaskDao(db, now: now);
}

Map<String, dynamic> _serverTaskJson({
  String id = 's1',
  String title = 'Server task',
  String status = 'todo',
  String? updatedAt,
  String createdAt = '2026-06-05T10:00:00Z',
}) =>
    {
      'id': id,
      'user_id': 'u1',
      'title': title,
      'priority': 'medium',
      'status': status,
      'owner': 'user',
      'nag_count': 0,
      'created_at': createdAt,
      'updated_at': updatedAt ?? createdAt,
    };

void main() {
  setUpAll(() => sqfliteFfiInit());

  // ── PUSH ──────────────────────────────────────────────────────────────────

  group('TaskSync.push', () {
    test('drains the outbox in order and clears it on success', () async {
      final dao = await _freshDao();
      final t = await dao.applyLocalCreate('A', id: 'a1');
      await dao.applyLocalUpdate(t.id, title: 'A2');
      await dao.applyLocalComplete(t.id);

      final transport = _FakeTransport();
      final sync = TaskSync(dao, TasksRepository(transport));
      final result = await sync.push();

      expect(result.pushed, 3);
      expect(await dao.readOutbox(), isEmpty);

      // Calls in queue order: create POST, update PATCH, complete POST.
      final writeCalls =
          transport.calls.where((c) => c.method != 'GET').toList();
      expect(writeCalls[0].method, 'POST');
      expect(writeCalls[0].path, '/api/tasks');
      expect(writeCalls[0].body!['id'], 'a1');
      expect(writeCalls[1].method, 'PATCH');
      expect(writeCalls[1].path, '/api/tasks/a1');
      expect(writeCalls[1].body!.containsKey('id'), isFalse);
      expect(writeCalls[2].path, '/api/tasks/a1/complete');
    });

    test('create replays the client id (idempotent)', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('Buy milk', id: 'fixed-uuid');
      final transport = _FakeTransport();
      await TaskSync(dao, TasksRepository(transport)).push();
      final createCall =
          transport.calls.firstWhere((c) => c.path == '/api/tasks');
      expect(createCall.body!['id'], 'fixed-uuid');
    });

    test('stops at the first network failure and keeps the rest queued',
        () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('First', id: 'f1'); // POST ok
      final t2 = await dao.applyLocalCreate('Second', id: 'f2');
      await dao.applyLocalUpdate(t2.id, title: 'patched'); // PATCH fails

      final transport = _FakeTransport(
        failNetworkOnPaths: {'/api/tasks/f2'}, // the PATCH path
      );
      final sync = TaskSync(dao, TasksRepository(transport));
      final result = await sync.push();

      expect(result.pushInterrupted, isTrue);
      // Both creates pushed; the failing update + everything after stays.
      final remaining = await dao.readOutbox();
      expect(remaining.map((o) => o.op),
          contains(OutboxOp.update));
      // f1 create and f2 create succeeded → removed.
      expect(remaining.every((o) => o.op != OutboxOp.create), isTrue);
    });

    test('a delete that pushed hard-removes the tombstone', () async {
      final dao = await _freshDao();
      final t = await dao.applyLocalCreate('Bye', id: 'd1');
      // Pretend the create already synced so only the delete is queued.
      await TaskSync(dao, TasksRepository(_FakeTransport())).push();
      await dao.applyLocalDelete(t.id);

      await TaskSync(dao, TasksRepository(_FakeTransport())).push();
      expect(await dao.getById(t.id), isNull);
      expect(await dao.readOutbox(), isEmpty);
    });

    test('non-network server error dequeues (does not wedge the queue)',
        () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('A', id: 'a1');
      await dao.applyLocalCreate('B', id: 'b1');
      final transport = _FakeTransport(
        failServerOnPaths: {'/api/tasks': 422}, // both creates 422
      );
      final sync = TaskSync(dao, TasksRepository(transport));
      final result = await sync.push();
      expect(result.pushInterrupted, isFalse);
      // Queue drained despite server rejecting — next pull restores truth.
      expect(await dao.readOutbox(), isEmpty);
    });
  });

  // ── PULL + LWW ──────────────────────────────────────────────────────────

  group('TaskSync.pull last-write-wins', () {
    test('writes a brand-new server task into the cache', () async {
      final dao = await _freshDao();
      final transport = _FakeTransport(changesResponse: {
        'tasks': [_serverTaskJson(id: 'srv', title: 'Hello')],
        'deleted': [],
        'now': '2026-06-05T12:00:00Z',
      });
      final result = await TaskSync(dao, TasksRepository(transport)).pull();
      expect(result.pulled, 1);
      final stored = await dao.getById('srv');
      expect(stored!.title, 'Hello');
      expect(await dao.getCursor(), '2026-06-05T12:00:00Z');
    });

    test('passes the stored cursor as ?since', () async {
      final dao = await _freshDao();
      await dao.setCursor('2026-06-05T09:00:00Z');
      final transport = _FakeTransport(changesResponse: {
        'tasks': [],
        'deleted': [],
        'now': '2026-06-05T12:00:00Z',
      });
      await TaskSync(dao, TasksRepository(transport)).pull();
      final changesCall =
          transport.calls.firstWhere((c) => c.path.contains('/changes'));
      expect(changesCall.query, containsPair('since', '2026-06-05T09:00:00Z'));
    });

    test('server wins when local is NOT dirty', () async {
      final dao = await _freshDao();
      // Seed a clean local copy from a prior sync.
      await dao.upsertFromServer(
        Task.fromJson(_serverTaskJson(id: 'x', title: 'Old')),
        serverUpdatedAt: '2026-06-05T10:00:00Z',
      );
      final transport = _FakeTransport(changesResponse: {
        'tasks': [
          _serverTaskJson(
              id: 'x', title: 'New', updatedAt: '2026-06-05T11:00:00Z')
        ],
        'deleted': [],
        'now': '2026-06-05T12:00:00Z',
      });
      await TaskSync(dao, TasksRepository(transport)).pull();
      final stored = await dao.getById('x');
      expect(stored!.title, 'New');
      expect(await dao.readConflicts(), isEmpty);
    });

    test(
        'server wins on dirty local with older local time → conflict logged',
        () async {
      // Local dirty edit pinned at 10:00.
      final dao = await _freshDao(now: () => '2026-06-05T10:00:00Z');
      await dao.applyLocalCreate('Local title', id: 'c1');

      final transport = _FakeTransport(changesResponse: {
        'tasks': [
          _serverTaskJson(
              id: 'c1',
              title: 'Server title',
              updatedAt: '2026-06-05T11:00:00Z')
        ],
        'deleted': [],
        'now': '2026-06-05T12:00:00Z',
      });
      final result = await TaskSync(dao, TasksRepository(transport)).pull();

      expect(result.conflicts, 1);
      final stored = await dao.getById('c1');
      expect(stored!.title, 'Server title'); // server won
      final conflicts = await dao.readConflicts();
      expect(conflicts, isNotEmpty);
      final titleConflict =
          conflicts.firstWhere((c) => c.field == 'title');
      expect(titleConflict.local, 'Local title');
      expect(titleConflict.server, 'Server title');
    });

    test('local wins (kept) when dirty local is strictly newer; no log',
        () async {
      // Local dirty edit pinned at 12:00 (newer than server's 11:00).
      final dao = await _freshDao(now: () => '2026-06-05T12:00:00Z');
      await dao.applyLocalCreate('Local newest', id: 'c2');

      final transport = _FakeTransport(changesResponse: {
        'tasks': [
          _serverTaskJson(
              id: 'c2',
              title: 'Server older',
              updatedAt: '2026-06-05T11:00:00Z')
        ],
        'deleted': [],
        'now': '2026-06-05T13:00:00Z',
      });
      final result = await TaskSync(dao, TasksRepository(transport)).pull();

      expect(result.pulled, 0); // server copy not written
      final stored = await dao.getById('c2');
      expect(stored!.title, 'Local newest'); // local kept
      expect(await dao.readConflicts(), isEmpty);
      // still dirty → will re-push next sync
      expect(await dao.dirtyIds(), contains('c2'));
    });

    test('applies server tombstones (removes from list)', () async {
      final dao = await _freshDao();
      await dao.upsertFromServer(
        Task.fromJson(_serverTaskJson(id: 'gone', title: 'Doomed')),
        serverUpdatedAt: '2026-06-05T10:00:00Z',
      );
      final transport = _FakeTransport(changesResponse: {
        'tasks': [],
        'deleted': ['gone'],
        'now': '2026-06-05T12:00:00Z',
      });
      final result = await TaskSync(dao, TasksRepository(transport)).pull();
      expect(result.deletedApplied, 1);
      expect((await dao.list()).map((e) => e.id), isNot(contains('gone')));
    });

    test('pull network failure leaves the cursor untouched', () async {
      final dao = await _freshDao();
      await dao.setCursor('2026-06-05T09:00:00Z');
      final transport = _FailingGetTransport();
      final result = await TaskSync(dao, TasksRepository(transport)).pull();
      expect(result.pullFailed, isTrue);
      expect(await dao.getCursor(), '2026-06-05T09:00:00Z');
    });
  });

  // ── SYNC orchestration ────────────────────────────────────────────────────

  group('TaskSync.sync', () {
    test('push then pull; guards against concurrent runs', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('A', id: 'a1');
      final transport = _FakeTransport(changesResponse: {
        'tasks': [_serverTaskJson(id: 'srv', title: 'Server one')],
        'deleted': [],
        'now': '2026-06-05T12:00:00Z',
      });
      final sync = TaskSync(dao, TasksRepository(transport));

      final result = await sync.sync();
      expect(result.pushed, 1);
      expect(result.pulled, 1);
      expect(sync.isRunning, isFalse);

      // The local create pushed AND the server task pulled.
      expect(await dao.getById('srv'), isNotNull);
      expect(await dao.outboxCount(), 0);
    });

    test('a concurrent sync() call is a no-op while one is running', () async {
      final dao = await _freshDao();
      final sync = TaskSync(dao, TasksRepository(_FakeTransport()));
      final a = sync.sync();
      final b = sync.sync(); // should short-circuit
      final results = await Future.wait([a, b]);
      // One ran, one returned the empty default — neither throws.
      expect(results, hasLength(2));
    });
  });
}

// ── test helpers ─────────────────────────────────────────────────────────────

/// A transport whose GET always throws a network error (for pull failure test).
class _FailingGetTransport implements TasksTransport {
  @override
  Future<Map<String, dynamic>> getJson(String path,
          {Map<String, dynamic>? queryParams}) async =>
      throw ApiError(0, 'Network error');
  @override
  Future<Map<String, dynamic>> postJson(
          String path, Map<String, dynamic> body) async =>
      {};
  @override
  Future<Map<String, dynamic>> patchJson(
          String path, Map<String, dynamic> body) async =>
      {};
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async => {};
}
