import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/note_dao.dart';
import 'package:lazyclaw_mobile/models/note.dart';
import 'package:lazyclaw_mobile/repositories/notes_repository.dart';
import 'package:lazyclaw_mobile/sync/note_sync.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

// ── Programmable fake transport ──────────────────────────────────────────────

class _Call {
  final String method;
  final String path;
  final Map<String, dynamic>? body;
  final Map<String, dynamic>? query;
  _Call(this.method, this.path, {this.body, this.query});
}

/// Records every call. `changesResponse` is returned by GET
/// /api/lazybrain/notes/changes; the *OnPaths maps make the matching write
/// throw the corresponding failure shape.
class _FakeTransport implements NotesTransport {
  final List<_Call> calls = [];
  Map<String, dynamic> changesResponse;

  /// Substring → throw network ApiError(0) when a write path contains it.
  final Set<String> failNetworkOnPaths;

  /// Substring → throw a non-network ApiError(status) on the write path.
  final Map<String, int> failServerOnPaths;

  /// Substring → throw a real [DioException] on the write path (mirrors what
  /// production actually throws: the `ApiError` lives in `DioException.error`).
  final Map<String, DioException Function()> dioErrorOnPaths;

  _FakeTransport({
    Map<String, dynamic>? changesResponse,
    Set<String>? failNetworkOnPaths,
    Map<String, int>? failServerOnPaths,
    Map<String, DioException Function()>? dioErrorOnPaths,
  })  : changesResponse =
            changesResponse ?? {'notes': [], 'deleted': [], 'now': ''},
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
    return {'notes': []};
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
Future<NoteDao> _freshDao({String Function()? now}) async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:notesyncmem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return NoteDao(db, now: now);
}

Map<String, dynamic> _serverNoteJson({
  String id = 's1',
  String? title = 'Server note',
  String content = 'Server content',
  int importance = 0,
  bool pinned = false,
  String? updatedAt,
  String createdAt = '2026-06-05T10:00:00Z',
}) =>
    {
      'id': id,
      'title': title,
      'content': content,
      'tags': <String>[],
      'importance': importance,
      'pinned': pinned,
      'trace_session_id': null,
      'title_key': null,
      'created_at': createdAt,
      'updated_at': updatedAt ?? createdAt,
    };

/// A production-shaped server-error DioException: a real HTTP response with a
/// status code, carrying the `ApiError` in `.error` exactly like the app's
/// `_ErrorInterceptor` rethrows it.
DioException _serverDio(int status) {
  final req = RequestOptions(path: '/api/lazybrain/notes');
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
  final req = RequestOptions(path: '/api/lazybrain/notes');
  return DioException(
    requestOptions: req,
    type: DioExceptionType.connectionError,
    error: ApiError(0, 'Network error'),
  );
}

void main() {
  setUpAll(() => sqfliteFfiInit());

  // ── PUSH ──────────────────────────────────────────────────────────────────

  group('NoteSync.push', () {
    test('drains the outbox in order and clears it on success', () async {
      final dao = await _freshDao();
      final n = await dao.applyLocalCreate(content: 'A', id: 'a1');
      await dao.applyLocalUpdate(n.id, title: 'A2');
      await dao.applyLocalDelete(n.id);

      final transport = _FakeTransport();
      final sync = NoteSync(dao, NotesRepository(transport));
      final result = await sync.push();

      expect(result.pushed, 3);
      expect(await dao.readOutbox(), isEmpty);

      // Calls in queue order: create POST, update PATCH, delete DELETE.
      final writeCalls =
          transport.calls.where((c) => c.method != 'GET').toList();
      expect(writeCalls[0].method, 'POST');
      expect(writeCalls[0].path, '/api/lazybrain/notes');
      expect(writeCalls[0].body!['id'], 'a1');
      expect(writeCalls[1].method, 'PATCH');
      expect(writeCalls[1].path, '/api/lazybrain/notes/a1');
      expect(writeCalls[1].body!.containsKey('id'), isFalse);
      expect(writeCalls[2].method, 'DELETE');
      expect(writeCalls[2].path, '/api/lazybrain/notes/a1');
    });

    test('create replays the client id (idempotent)', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate(content: 'Buy milk', id: 'fixed-uuid');
      final transport = _FakeTransport();
      await NoteSync(dao, NotesRepository(transport)).push();
      final createCall = transport.calls
          .firstWhere((c) => c.path == '/api/lazybrain/notes');
      expect(createCall.body!['id'], 'fixed-uuid');
    });

    test('stops at the first network failure and keeps the rest queued',
        () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate(content: 'First', id: 'f1'); // POST ok
      final n2 = await dao.applyLocalCreate(content: 'Second', id: 'f2');
      await dao.applyLocalUpdate(n2.id, title: 'patched'); // PATCH fails

      final transport = _FakeTransport(
        failNetworkOnPaths: {'/api/lazybrain/notes/f2'}, // the PATCH path
      );
      final sync = NoteSync(dao, NotesRepository(transport));
      final result = await sync.push();

      expect(result.pushInterrupted, isTrue);
      final remaining = await dao.readOutbox();
      expect(remaining.map((o) => o.op), contains(NoteOutboxOp.update));
      // f1 create and f2 create succeeded → removed.
      expect(remaining.every((o) => o.op != NoteOutboxOp.create), isTrue);
    });

    test('a delete that pushed hard-removes the tombstone', () async {
      final dao = await _freshDao();
      final n = await dao.applyLocalCreate(content: 'Bye', id: 'd1');
      // Pretend the create already synced so only the delete is queued.
      await NoteSync(dao, NotesRepository(_FakeTransport())).push();
      await dao.applyLocalDelete(n.id);

      await NoteSync(dao, NotesRepository(_FakeTransport())).push();
      expect(await dao.getById(n.id), isNull);
      expect(await dao.readOutbox(), isEmpty);
    });

    test('non-network server error dequeues (does not wedge the queue)',
        () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate(content: 'A', id: 'a1');
      await dao.applyLocalCreate(content: 'B', id: 'b1');
      final transport = _FakeTransport(
        failServerOnPaths: {'/api/lazybrain/notes': 422}, // both creates 422
      );
      final sync = NoteSync(dao, NotesRepository(transport));
      final result = await sync.push();
      expect(result.pushInterrupted, isFalse);
      expect(await dao.readOutbox(), isEmpty);
    });

    // ── production DioException error-classification regression guards ──

    test(
        'a real DioException(badResponse, error: ApiError(500)) RETAINS the '
        'outbox item (never silently dropped)', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate(content: 'Keep me', id: 'srv5xx');
      // Production throws DioException with the ApiError nested in `.error`.
      final transport = _FakeTransport(
        dioErrorOnPaths: {'/api/lazybrain/notes': () => _serverDio(500)},
      );
      final sync = NoteSync(dao, NotesRepository(transport));
      final result = await sync.push();

      // 5xx is retryable → drain stops, the item stays queued.
      expect(result.pushInterrupted, isTrue);
      expect(result.pushed, 0);
      final remaining = await dao.readOutbox();
      expect(remaining, hasLength(1));
      expect(remaining.first.op, NoteOutboxOp.create);
      // The attempt was counted (so it can eventually be dead-lettered).
      expect(remaining.first.attempts, 1);
    });

    test(
        'a connection-type DioException stops the drain and preserves the '
        'whole queue (_PushInterrupted semantics)', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate(content: 'First', id: 'n1'); // POST → ok
      final n2 = await dao.applyLocalCreate(content: 'Second', id: 'n2');
      await dao.applyLocalUpdate(n2.id, title: 'patched'); // PATCH /notes/n2

      final transport = _FakeTransport(
        dioErrorOnPaths: {'/api/lazybrain/notes/n2': () => _connectionDio()},
      );
      final result = await NoteSync(dao, NotesRepository(transport)).push();

      expect(result.pushInterrupted, isTrue);
      final remaining = await dao.readOutbox();
      expect(remaining.map((o) => o.op), contains(NoteOutboxOp.update));
      expect(remaining.every((o) => o.op != NoteOutboxOp.create), isTrue);
      // No attempt counter bumped for a network error (only 5xx counts).
      expect(
          remaining.firstWhere((o) => o.op == NoteOutboxOp.update).attempts, 0);
    });

    test('a real DioException 404 on delete is treated as success (drains)',
        () async {
      final dao = await _freshDao();
      final n = await dao.applyLocalCreate(content: 'Bye', id: 'gone404');
      await NoteSync(dao, NotesRepository(_FakeTransport())).push();
      await dao.applyLocalDelete(n.id);

      final transport = _FakeTransport(
        dioErrorOnPaths: {'/api/lazybrain/notes/gone404': () => _serverDio(404)},
      );
      final result = await NoteSync(dao, NotesRepository(transport)).push();
      expect(result.pushInterrupted, isFalse);
      expect(result.pushed, 1); // 404-on-delete counted as a successful drain
      expect(await dao.readOutbox(), isEmpty);
      expect(await dao.getById(n.id), isNull); // tombstone hard-removed
    });

    test('a real DioException 422 (client error) drains the item', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate(content: 'Bad', id: 'bad422');
      final transport = _FakeTransport(
        dioErrorOnPaths: {'/api/lazybrain/notes': () => _serverDio(422)},
      );
      final result = await NoteSync(dao, NotesRepository(transport)).push();
      expect(result.pushInterrupted, isFalse);
      expect(await dao.readOutbox(), isEmpty); // definitive 4xx → safe to drain
    });

    test('a 5xx item dead-letters after kMaxPushAttempts, never wedges',
        () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate(content: 'Poison', id: 'p1');
      final transport = _FakeTransport(
        dioErrorOnPaths: {'/api/lazybrain/notes': () => _serverDio(503)},
      );
      for (var i = 0; i < NoteSync.kMaxPushAttempts; i++) {
        await NoteSync(dao, NotesRepository(transport)).push();
      }
      // After the last attempt the poison item is dead-lettered — the queue is
      // never wedged. (Mirrors task_sync: the post-drain commitPush retires the
      // cache row; the next pull re-establishes server truth.)
      expect(await dao.readOutbox(), isEmpty);
      // The cache row itself still survives (it is NOT lost).
      expect(await dao.getById('p1'), isNotNull);
    });

    // ── atomic push commit (idempotent replay) ──

    test('commitPush is idempotent — replaying a retired item is a no-op',
        () async {
      final dao = await _freshDao();
      final n = await dao.applyLocalCreate(content: 'A', id: 'c2a');
      final seq = (await dao.readOutbox()).first.seq;
      await dao.commitPush(seq, n.id);
      expect(await dao.readOutbox(), isEmpty);
      expect(await dao.dirtyIds(), isEmpty);
      await dao.commitPush(seq, n.id);
      expect(await dao.dirtyIds(), isEmpty);
      expect(await dao.getById(n.id), isNotNull);
    });

    // ── partial-update coalescing + client updated_at on PATCH ──

    test(
        'multiple pending updates for one id coalesce into ONE PATCH carrying '
        'the latest fields + client updated_at', () async {
      final dao = await _freshDao();
      final n = await dao.applyLocalCreate(content: 'Body', id: 'h3');
      // Pretend the create already synced so only updates remain queued.
      await NoteSync(dao, NotesRepository(_FakeTransport())).push();
      await dao.applyLocalUpdate(n.id, title: 'First edit');
      await dao.applyLocalUpdate(n.id, content: 'Second edit');
      await dao.applyLocalUpdate(n.id, title: 'Final title');

      final transport = _FakeTransport();
      final result = await NoteSync(dao, NotesRepository(transport)).push();

      expect(await dao.readOutbox(), isEmpty);
      final patches =
          transport.calls.where((c) => c.method == 'PATCH').toList();
      expect(patches, hasLength(1));
      expect(patches.first.body!['title'], 'Final title');
      expect(patches.first.body!['content'], 'Second edit');
      expect(patches.first.body!.containsKey('updated_at'), isTrue);
      expect(patches.first.body!.containsKey('id'), isFalse);
      expect(result.pushed, 1);
    });
  });

  // ── PULL + LWW ──────────────────────────────────────────────────────────

  group('NoteSync.pull last-write-wins', () {
    test('writes a brand-new server note into the cache', () async {
      final dao = await _freshDao();
      final transport = _FakeTransport(changesResponse: {
        'notes': [_serverNoteJson(id: 'srv', content: 'Hello')],
        'deleted': [],
        'now': '2026-06-05T12:00:00Z',
      });
      final result = await NoteSync(dao, NotesRepository(transport)).pull();
      expect(result.pulled, 1);
      final stored = await dao.getById('srv');
      expect(stored!.content, 'Hello');
      expect(await dao.getCursor(), '2026-06-05T12:00:00Z');
    });

    test('passes the stored cursor as ?since to the notes changes endpoint',
        () async {
      final dao = await _freshDao();
      await dao.setCursor('2026-06-05T09:00:00Z');
      final transport = _FakeTransport(changesResponse: {
        'notes': [],
        'deleted': [],
        'now': '2026-06-05T12:00:00Z',
      });
      await NoteSync(dao, NotesRepository(transport)).pull();
      final changesCall =
          transport.calls.firstWhere((c) => c.path.contains('/changes'));
      expect(changesCall.path, '/api/lazybrain/notes/changes');
      expect(changesCall.query, containsPair('since', '2026-06-05T09:00:00Z'));
    });

    test('server wins when local is NOT dirty', () async {
      final dao = await _freshDao();
      await dao.upsertFromServer(
        Note.fromJson(_serverNoteJson(id: 'x', content: 'Old')),
        serverUpdatedAt: '2026-06-05T10:00:00Z',
      );
      final transport = _FakeTransport(changesResponse: {
        'notes': [
          _serverNoteJson(
              id: 'x', content: 'New', updatedAt: '2026-06-05T11:00:00Z')
        ],
        'deleted': [],
        'now': '2026-06-05T12:00:00Z',
      });
      await NoteSync(dao, NotesRepository(transport)).pull();
      final stored = await dao.getById('x');
      expect(stored!.content, 'New');
      expect(await dao.readConflicts(), isEmpty);
    });

    test('server wins on dirty local with older local time → conflict logged',
        () async {
      final dao = await _freshDao(now: () => '2026-06-05T10:00:00Z');
      await dao.applyLocalCreate(content: 'Local body', id: 'c1');

      final transport = _FakeTransport(changesResponse: {
        'notes': [
          _serverNoteJson(
              id: 'c1',
              content: 'Server body',
              updatedAt: '2026-06-05T11:00:00Z')
        ],
        'deleted': [],
        'now': '2026-06-05T12:00:00Z',
      });
      final result = await NoteSync(dao, NotesRepository(transport)).pull();

      expect(result.conflicts, 1);
      final stored = await dao.getById('c1');
      expect(stored!.content, 'Server body'); // server won
      final conflicts = await dao.readConflicts();
      final contentConflict =
          conflicts.firstWhere((c) => c.field == 'content');
      expect(contentConflict.local, 'Local body');
      expect(contentConflict.server, 'Server body');
    });

    test('local wins (kept) when dirty local is strictly newer; no log',
        () async {
      final dao = await _freshDao(now: () => '2026-06-05T12:00:00Z');
      await dao.applyLocalCreate(content: 'Local newest', id: 'c2');

      final transport = _FakeTransport(changesResponse: {
        'notes': [
          _serverNoteJson(
              id: 'c2',
              content: 'Server older',
              updatedAt: '2026-06-05T11:00:00Z')
        ],
        'deleted': [],
        'now': '2026-06-05T13:00:00Z',
      });
      final result = await NoteSync(dao, NotesRepository(transport)).pull();

      expect(result.pulled, 0); // server copy not written
      final stored = await dao.getById('c2');
      expect(stored!.content, 'Local newest'); // local kept
      expect(await dao.readConflicts(), isEmpty);
      expect(await dao.dirtyIds(), contains('c2'));
    });

    test('applies server tombstones (removes from list)', () async {
      final dao = await _freshDao();
      await dao.upsertFromServer(
        Note.fromJson(_serverNoteJson(id: 'gone', content: 'Doomed')),
        serverUpdatedAt: '2026-06-05T10:00:00Z',
      );
      final transport = _FakeTransport(changesResponse: {
        'notes': [],
        'deleted': ['gone'],
        'now': '2026-06-05T12:00:00Z',
      });
      final result = await NoteSync(dao, NotesRepository(transport)).pull();
      expect(result.deletedApplied, 1);
      expect((await dao.list()).map((e) => e.id), isNot(contains('gone')));
    });

    test('pull network failure leaves the cursor untouched', () async {
      final dao = await _freshDao();
      await dao.setCursor('2026-06-05T09:00:00Z');
      final transport = _FailingGetTransport();
      final result = await NoteSync(dao, NotesRepository(transport)).pull();
      expect(result.pullFailed, isTrue);
      expect(await dao.getCursor(), '2026-06-05T09:00:00Z');
    });

    // ── server tombstone vs an unsynced local edit ──

    test(
        'server delete of a DIRTY local row logs a conflict + reconciles the '
        'queued outbox op (delete-wins, never silent)', () async {
      final dao = await _freshDao(now: () => '2026-06-05T10:00:00Z');
      await dao.applyLocalCreate(content: 'My unsynced note', id: 'h1');
      expect(await dao.outboxCount(), 1);

      final transport = _FakeTransport(changesResponse: {
        'notes': [],
        'deleted': ['h1'],
        'now': '2026-06-05T12:00:00Z',
      });
      final result = await NoteSync(dao, NotesRepository(transport)).pull();

      expect(result.deletedApplied, 1);
      expect(result.conflicts, greaterThanOrEqualTo(1));
      final conflicts = await dao.readConflicts();
      final contentConflict =
          conflicts.firstWhere((c) => c.field == 'content');
      expect(contentConflict.local, 'My unsynced note');
      expect(contentConflict.server, isNull); // server-deleted → no server value
      expect(await dao.outboxCount(), 0); // queued create reconciled away
      expect((await dao.list()).map((e) => e.id), isNot(contains('h1')));
    });

    test('server delete of a CLEAN local row applies silently (no conflict)',
        () async {
      final dao = await _freshDao();
      await dao.upsertFromServer(
        Note.fromJson(_serverNoteJson(id: 'clean', content: 'Synced')),
        serverUpdatedAt: '2026-06-05T10:00:00Z',
      );
      final transport = _FakeTransport(changesResponse: {
        'notes': [],
        'deleted': ['clean'],
        'now': '2026-06-05T12:00:00Z',
      });
      final result = await NoteSync(dao, NotesRepository(transport)).pull();
      expect(result.deletedApplied, 1);
      expect(result.conflicts, 0);
      expect(await dao.readConflicts(), isEmpty);
    });

    // ── conflict-table dedup ──

    test('re-observing the SAME conflict does not duplicate the row', () async {
      final dao = await _freshDao(now: () => '2026-06-05T10:00:00Z');
      await dao.applyLocalCreate(content: 'Local body', id: 'm1');
      final changes = {
        'notes': [
          _serverNoteJson(
              id: 'm1',
              content: 'Server body',
              updatedAt: '2026-06-05T11:00:00Z')
        ],
        'deleted': [],
        'now': '2026-06-05T12:00:00Z',
      };
      await NoteSync(dao,
              NotesRepository(_FakeTransport(changesResponse: changes)))
          .pull();
      final afterFirst = await dao.readConflicts();
      await dao.applyLocalUpdate('m1', content: 'Local body');
      await NoteSync(dao,
              NotesRepository(_FakeTransport(changesResponse: changes)))
          .pull();
      final afterSecond = await dao.readConflicts();
      final contentRows =
          afterSecond.where((c) => c.field == 'content').toList();
      expect(contentRows,
          hasLength(afterFirst.where((c) => c.field == 'content').length));
    });

    // ── empty server `now` ──

    test('empty `now` with no datable rows → pull FAILS, cursor untouched',
        () async {
      final dao = await _freshDao();
      await dao.setCursor('2026-06-05T09:00:00Z');
      final transport = _FakeTransport(changesResponse: {
        'notes': [],
        'deleted': [],
        'now': '', // server omitted its clock
      });
      final result = await NoteSync(dao, NotesRepository(transport)).pull();
      expect(result.pullFailed, isTrue);
      expect(await dao.getCursor(), '2026-06-05T09:00:00Z');
    });

    test('empty `now` falls back to the max row updated_at', () async {
      final dao = await _freshDao();
      final transport = _FakeTransport(changesResponse: {
        'notes': [
          _serverNoteJson(id: 'a', updatedAt: '2026-06-05T11:00:00Z'),
          _serverNoteJson(id: 'b', updatedAt: '2026-06-05T13:00:00Z'),
        ],
        'deleted': [],
        'now': '', // no server clock → fall back to newest updated_at
      });
      final result = await NoteSync(dao, NotesRepository(transport)).pull();
      expect(result.pullFailed, isFalse);
      expect(result.pulled, 2);
      expect(await dao.getCursor(), '2026-06-05T13:00:00Z');
    });
  });

  // ── SYNC orchestration ────────────────────────────────────────────────────

  group('NoteSync.sync', () {
    test('push then pull; guards against concurrent runs', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate(content: 'A', id: 'a1');
      final transport = _FakeTransport(changesResponse: {
        'notes': [_serverNoteJson(id: 'srv', content: 'Server one')],
        'deleted': [],
        'now': '2026-06-05T12:00:00Z',
      });
      final sync = NoteSync(dao, NotesRepository(transport));

      final result = await sync.sync();
      expect(result.pushed, 1);
      expect(result.pulled, 1);
      expect(sync.isRunning, isFalse);

      expect(await dao.getById('srv'), isNotNull);
      expect(await dao.outboxCount(), 0);
    });

    test('a concurrent sync() call is a no-op while one is running', () async {
      final dao = await _freshDao();
      final sync = NoteSync(dao, NotesRepository(_FakeTransport()));
      final a = sync.sync();
      final b = sync.sync(); // should short-circuit
      final results = await Future.wait([a, b]);
      expect(results, hasLength(2));
    });
  });
}

// ── test helpers ─────────────────────────────────────────────────────────────

/// A transport whose GET always throws a network error (for pull failure test).
class _FailingGetTransport implements NotesTransport {
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
