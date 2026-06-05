import 'package:dio/dio.dart';
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

  /// Substring → throw a real [DioException] on the write path (mirrors what
  /// production actually throws: the `ApiError` lives in `DioException.error`).
  /// Each entry returns a fresh exception so the path/type can be asserted.
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

/// A connection-type DioException (no response reached us) — the network-down
/// shape. `.error` carries ApiError(0) like the interceptor would produce.
DioException _connectionDio() {
  final req = RequestOptions(path: '/api/tasks');
  return DioException(
    requestOptions: req,
    type: DioExceptionType.connectionError,
    error: ApiError(0, 'Network error'),
  );
}

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

    // ── C1: production DioException error-classification regression guards ──

    test(
        'C1: a real DioException(badResponse, error: ApiError(500)) RETAINS the '
        'outbox item (never silently dropped)', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('Keep me', id: 'srv5xx');
      // Production throws DioException with the ApiError nested in `.error`.
      final transport = _FakeTransport(
        dioErrorOnPaths: {'/api/tasks': () => _serverDio(500)},
      );
      final sync = TaskSync(dao, TasksRepository(transport));
      final result = await sync.push();

      // 5xx is retryable → drain stops, the item stays queued.
      expect(result.pushInterrupted, isTrue);
      expect(result.pushed, 0);
      final remaining = await dao.readOutbox();
      expect(remaining, hasLength(1));
      expect(remaining.first.op, OutboxOp.create);
      // The attempt was counted (so it can eventually be dead-lettered).
      expect(remaining.first.attempts, 1);
    });

    test(
        'C1: a connection-type DioException stops the drain and preserves the '
        'whole queue (_PushInterrupted semantics)', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('First', id: 'n1'); // POST /api/tasks → ok
      final t2 = await dao.applyLocalCreate('Second', id: 'n2');
      await dao.applyLocalUpdate(t2.id, title: 'patched'); // PATCH /api/tasks/n2

      // The n2 PATCH hits a network drop (connection-type DioException). Both
      // creates POST to /api/tasks and succeed first.
      final transport = _FakeTransport(
        dioErrorOnPaths: {'/api/tasks/n2': () => _connectionDio()},
      );
      final result = await TaskSync(dao, TasksRepository(transport)).push();

      expect(result.pushInterrupted, isTrue);
      // Both creates drained; the failing update stays queued (queue preserved).
      final remaining = await dao.readOutbox();
      expect(remaining.map((o) => o.op), contains(OutboxOp.update));
      expect(remaining.every((o) => o.op != OutboxOp.create), isTrue);
      // No attempt counter bumped for a network error (only 5xx counts).
      expect(remaining.firstWhere((o) => o.op == OutboxOp.update).attempts, 0);
    });

    test('C1: a real DioException 404 on delete is treated as success (drains)',
        () async {
      final dao = await _freshDao();
      final t = await dao.applyLocalCreate('Bye', id: 'gone404');
      await TaskSync(dao, TasksRepository(_FakeTransport())).push();
      await dao.applyLocalDelete(t.id);

      final transport = _FakeTransport(
        dioErrorOnPaths: {'/api/tasks/gone404': () => _serverDio(404)},
      );
      final result = await TaskSync(dao, TasksRepository(transport)).push();
      expect(result.pushInterrupted, isFalse);
      expect(result.pushed, 1); // 404-on-delete counted as a successful drain
      expect(await dao.readOutbox(), isEmpty);
      expect(await dao.getById(t.id), isNull); // tombstone hard-removed
    });

    test('C1: a real DioException 422 (client error) drains the item', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('Bad', id: 'bad422');
      final transport = _FakeTransport(
        dioErrorOnPaths: {'/api/tasks': () => _serverDio(422)},
      );
      final result = await TaskSync(dao, TasksRepository(transport)).push();
      expect(result.pushInterrupted, isFalse);
      expect(await dao.readOutbox(), isEmpty); // definitive 4xx → safe to drain
    });

    // ── create_rejected conflict (parity with BudgetsSync) ──

    test(
        'create_rejected: a 4xx on a CREATE logs a conflict and keeps the cache '
        'row dirty (does NOT silently vanish)', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('My task', id: 'rej1');
      final transport = _FakeTransport(
        dioErrorOnPaths: {'/api/tasks': () => _serverDio(422)},
      );
      final result = await TaskSync(dao, TasksRepository(transport)).push();

      // Outbox row drained (no longer blocking the queue).
      expect(result.pushInterrupted, isFalse);
      expect(await dao.readOutbox(), isEmpty);

      // But the cache row stays dirty — the user's data is NOT silently lost.
      expect(await dao.dirtyIds(), contains('rej1'));

      // A create_rejected conflict row must have been written.
      final conflicts = await dao.readConflicts();
      expect(conflicts, isNotEmpty);
      final rejected =
          conflicts.where((c) => c.field == 'create_rejected').toList();
      expect(rejected, hasLength(1));
      expect(rejected.first.id, 'rej1');
      // local carries the HTTP status so the user knows WHY it was rejected.
      expect(rejected.first.local, contains('422'));
      // server is null — the create never landed.
      expect(rejected.first.server, isNull);
    });

    test(
        'create_rejected: an update 4xx does NOT log create_rejected '
        '(drains silently — pull restores truth)', () async {
      final dao = await _freshDao();
      final t = await dao.applyLocalCreate('A', id: 'upd1');
      // Drain the create successfully first.
      await TaskSync(dao, TasksRepository(_FakeTransport())).push();
      await dao.applyLocalUpdate(t.id, title: 'Updated');

      final transport = _FakeTransport(
        dioErrorOnPaths: {'/api/tasks/upd1': () => _serverDio(422)},
      );
      await TaskSync(dao, TasksRepository(transport)).push();

      // No create_rejected conflict should be written for an update op.
      final conflicts = await dao.readConflicts();
      expect(
          conflicts.where((c) => c.field == 'create_rejected'), isEmpty);
    });

    test('C1: a 5xx item dead-letters after kMaxPushAttempts, never wedges',
        () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('Poison', id: 'p1');
      final transport = _FakeTransport(
        dioErrorOnPaths: {'/api/tasks': () => _serverDio(503)},
      );
      // Drive enough sync attempts to exhaust the retry budget.
      for (var i = 0; i < TaskSync.kMaxPushAttempts; i++) {
        await TaskSync(dao, TasksRepository(transport)).push();
      }
      // After the last attempt the poison item is dead-lettered.
      expect(await dao.readOutbox(), isEmpty);
    });

    // ── C2: atomic push commit (idempotent replay) ──

    test('C2: commitPush is idempotent — replaying a retired item is a no-op',
        () async {
      final dao = await _freshDao();
      final t = await dao.applyLocalCreate('A', id: 'c2a');
      final outbox = await dao.readOutbox();
      final seq = outbox.first.seq;
      // First commit retires the item.
      await dao.commitPush(seq, t.id);
      expect(await dao.readOutbox(), isEmpty);
      expect(await dao.dirtyIds(), isEmpty);
      // Replaying the same commit (simulating a crash-retry) must not throw and
      // must leave state correct.
      await dao.commitPush(seq, t.id);
      expect(await dao.dirtyIds(), isEmpty);
      expect(await dao.getById(t.id), isNotNull); // row still present + clean
    });

    // ── H3: partial-update coalescing + client updated_at on PATCH ──

    test(
        'H3: multiple pending updates for one id coalesce into ONE PATCH '
        'carrying the latest fields + client updated_at', () async {
      final dao = await _freshDao();
      final t = await dao.applyLocalCreate('Title', id: 'h3');
      // Pretend the create already synced so only updates remain queued.
      await TaskSync(dao, TasksRepository(_FakeTransport())).push();
      await dao.applyLocalUpdate(t.id, title: 'First edit');
      await dao.applyLocalUpdate(t.id, description: 'Second edit');
      await dao.applyLocalUpdate(t.id, title: 'Final title');

      final transport = _FakeTransport();
      final result = await TaskSync(dao, TasksRepository(transport)).push();

      // All three update rows are gone from the outbox.
      expect(await dao.readOutbox(), isEmpty);
      // Exactly ONE PATCH hit the wire (the others were coalesced + dequeued).
      final patches =
          transport.calls.where((c) => c.method == 'PATCH').toList();
      expect(patches, hasLength(1));
      // Latest-wins fields are merged into the single payload.
      expect(patches.first.body!['title'], 'Final title');
      expect(patches.first.body!['description'], 'Second edit');
      // The client updated_at is included so the server can honor client LWW.
      expect(patches.first.body!.containsKey('updated_at'), isTrue);
      // And `id` is still stripped from the PATCH body.
      expect(patches.first.body!.containsKey('id'), isFalse);
      // `pushed` counts the single network write.
      expect(result.pushed, 1);
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

    // ── H1: server tombstone vs an unsynced local edit ──

    test(
        'H1: server delete of a DIRTY local row logs a conflict + reconciles '
        'the queued outbox op (delete-wins, never silent)', () async {
      final dao = await _freshDao(now: () => '2026-06-05T10:00:00Z');
      // A local dirty create (unsynced) for the id the server is deleting.
      await dao.applyLocalCreate('My unsynced note', id: 'h1');
      expect(await dao.outboxCount(), 1);

      final transport = _FakeTransport(changesResponse: {
        'tasks': [],
        'deleted': ['h1'],
        'now': '2026-06-05T12:00:00Z',
      });
      final result = await TaskSync(dao, TasksRepository(transport)).pull();

      // Delete applied, conflict logged, the moot outbox op reconciled away.
      expect(result.deletedApplied, 1);
      expect(result.conflicts, greaterThanOrEqualTo(1));
      final conflicts = await dao.readConflicts();
      final titleConflict = conflicts.firstWhere((c) => c.field == 'title');
      expect(titleConflict.local, 'My unsynced note');
      expect(titleConflict.server, isNull); // server-deleted → no server value
      expect(await dao.outboxCount(), 0); // queued create reconciled away
      expect((await dao.list()).map((e) => e.id), isNot(contains('h1')));
    });

    test('H1: server delete of a CLEAN local row applies silently (no conflict)',
        () async {
      final dao = await _freshDao();
      await dao.upsertFromServer(
        Task.fromJson(_serverTaskJson(id: 'clean', title: 'Synced')),
        serverUpdatedAt: '2026-06-05T10:00:00Z',
      );
      final transport = _FakeTransport(changesResponse: {
        'tasks': [],
        'deleted': ['clean'],
        'now': '2026-06-05T12:00:00Z',
      });
      final result = await TaskSync(dao, TasksRepository(transport)).pull();
      expect(result.deletedApplied, 1);
      expect(result.conflicts, 0);
      expect(await dao.readConflicts(), isEmpty);
    });

    // ── M1: conflict-table dedup ──

    test('M1: re-observing the SAME conflict does not duplicate the row',
        () async {
      final dao = await _freshDao(now: () => '2026-06-05T10:00:00Z');
      await dao.applyLocalCreate('Local title', id: 'm1');
      final changes = {
        'tasks': [
          _serverTaskJson(
              id: 'm1', title: 'Server title', updatedAt: '2026-06-05T11:00:00Z')
        ],
        'deleted': [],
        'now': '2026-06-05T12:00:00Z',
      };
      // First pull logs the conflict and writes the server copy.
      await TaskSync(dao, TasksRepository(_FakeTransport(changesResponse: changes)))
          .pull();
      final afterFirst = await dao.readConflicts();
      // Make the row dirty again with the SAME local value vs the same server.
      await dao.applyLocalUpdate('m1', title: 'Local title');
      await TaskSync(dao, TasksRepository(_FakeTransport(changesResponse: changes)))
          .pull();
      final afterSecond = await dao.readConflicts();
      // No duplicate identical (id, field, local, server) rows accumulated.
      final titleRows =
          afterSecond.where((c) => c.field == 'title').toList();
      expect(titleRows, hasLength(afterFirst.where((c) => c.field == 'title').length));
    });

    // ── M3: empty server `now` ──

    test('M3: empty `now` with no datable rows → pull FAILS, cursor untouched',
        () async {
      final dao = await _freshDao();
      await dao.setCursor('2026-06-05T09:00:00Z');
      final transport = _FakeTransport(changesResponse: {
        'tasks': [],
        'deleted': [],
        'now': '', // server omitted its clock
      });
      final result = await TaskSync(dao, TasksRepository(transport)).pull();
      expect(result.pullFailed, isTrue);
      // Cursor must NOT advance (we'd otherwise skip the delta forever).
      expect(await dao.getCursor(), '2026-06-05T09:00:00Z');
    });

    test('M3: empty `now` falls back to the max row updated_at', () async {
      final dao = await _freshDao();
      final transport = _FakeTransport(changesResponse: {
        'tasks': [
          _serverTaskJson(id: 'a', updatedAt: '2026-06-05T11:00:00Z'),
          _serverTaskJson(id: 'b', updatedAt: '2026-06-05T13:00:00Z'),
        ],
        'deleted': [],
        'now': '', // no server clock → fall back to newest updated_at
      });
      final result = await TaskSync(dao, TasksRepository(transport)).pull();
      expect(result.pullFailed, isFalse);
      expect(result.pulled, 2);
      expect(await dao.getCursor(), '2026-06-05T13:00:00Z');
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
