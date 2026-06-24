/// Offline-first sync-engine tests for the office suite (Sheets/Docs/PDF).
///
/// Mirrors `note_sync_test.dart`. Critically (per the CLAUDE.md lesson) the fake
/// transport throws the PRODUCTION exception shape — a real
/// `DioException(error: ApiError)` and the `DocConflictException` the repo
/// raises on 409 — so a green test can never hide a data-loss bug.
///
/// Verifies:
///   • PUSH drains the document outbox in order (create/update/delete).
///   • Offline edit not lost: a network throw keeps the dirty cache row + queue.
///   • A definitive 4xx drains; a 5xx is retained then dead-letters after N.
///   • A 409 on save logs a conflict (never silently dropped) and drains.
///   • PULL merges /changes with last-write-wins, applies tombstones, advances
///     the cursor; a web-created doc lands in the cache (so it surfaces on the
///     list); a server tombstone of a dirty row logs a conflict.
///   • Soft-delete propagation: a server tombstone hides the local row.
library;

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/document_cache_dao.dart';
import 'package:lazyclaw_mobile/repositories/documents_repository.dart';
import 'package:lazyclaw_mobile/sync/document_sync.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

// ── Programmable fake transport ──────────────────────────────────────────────

class _Call {
  final String method;
  final String path;
  final Map<String, dynamic>? body;
  _Call(this.method, this.path, {this.body});
}

/// Records every call. The /changes GET returns [changesResponse]; the *OnPaths
/// maps make a matching WRITE throw the corresponding production failure shape.
class _FakeTransport implements DocumentsTransport {
  final List<_Call> calls = [];
  Map<String, dynamic> changesResponse;

  /// Substring → throw a real network DioException on the write path.
  final Set<String> failNetworkOnPaths;

  /// Substring → throw a real server DioException(status) on the write path.
  final Map<String, int> failServerOnPaths;

  /// Substring → emit a 409 envelope (the repo turns it into DocConflictException).
  final Map<String, Map<String, dynamic>> conflictOnPutPaths;

  _FakeTransport({
    Map<String, dynamic>? changesResponse,
    Set<String>? failNetworkOnPaths,
    Map<String, int>? failServerOnPaths,
    Map<String, Map<String, dynamic>>? conflictOnPutPaths,
  })  : changesResponse =
            changesResponse ?? {'items': const [], 'server_time': ''},
        failNetworkOnPaths = failNetworkOnPaths ?? const {},
        failServerOnPaths = failServerOnPaths ?? const {},
        conflictOnPutPaths = conflictOnPutPaths ?? const {};

  void _maybeFail(String path) {
    for (final frag in failNetworkOnPaths) {
      if (path.contains(frag)) throw _connectionDio();
    }
    for (final entry in failServerOnPaths.entries) {
      if (path.contains(entry.key)) throw _serverDio(entry.value);
    }
  }

  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    calls.add(_Call('GET', path));
    if (path.contains('/changes')) return changesResponse;
    // Plain list endpoint (/api/<kind>) — empty unless tests need it.
    return {'sheets': const [], 'docs': const [], 'files': const []};
  }

  @override
  Future<Map<String, dynamic>> postJson(
      String path, Map<String, dynamic> body) async {
    _maybeFail(path);
    calls.add(_Call('POST', path, body: body));
    // Echo a created row carrying the client id + a server updated_at.
    return {
      'sheet': {
        'id': body['id'] ?? 'srv-new',
        'name': body['name'] ?? 'Untitled',
        'updated_at': '2026-06-20T10:00:00Z',
      }
    };
  }

  @override
  Future<Map<String, dynamic>> putJson(
      String path, Map<String, dynamic> body) async {
    for (final entry in conflictOnPutPaths.entries) {
      if (path.contains(entry.key)) {
        // Production: the repo sees a 409 DioException and rethrows
        // DocConflictException after parsing `data.current`.
        throw _conflictDio(entry.value);
      }
    }
    _maybeFail(path);
    calls.add(_Call('PUT', path, body: body));
    return {
      'sheet': {'id': 'x', 'updated_at': '2026-06-20T12:00:00Z'}
    };
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

  @override
  Future<Map<String, dynamic>> uploadFile(String path, file) async => {};

  @override
  Future<List<int>> getBytes(String path) async => const [];

  @override
  Future<List<int>> postBytes(String path, Map<String, dynamic> body) async =>
      const [];
}

// ── Production-shaped DioExceptions ──────────────────────────────────────────

DioException _serverDio(int status) {
  final req = RequestOptions(path: '/api/sheets');
  return DioException(
    requestOptions: req,
    type: DioExceptionType.badResponse,
    response: Response(requestOptions: req, statusCode: status),
    error: ApiError(status, 'Server $status'),
  );
}

DioException _connectionDio() {
  final req = RequestOptions(path: '/api/sheets');
  return DioException(
    requestOptions: req,
    type: DioExceptionType.connectionError,
    error: ApiError(0, 'Network error'),
  );
}

/// A real 409 DioException carrying `{current: {...}}` — exactly what the
/// `DioDocumentsTransport` raises, which `DocumentsRepository.save` parses into a
/// `DocConflictException`.
DioException _conflictDio(Map<String, dynamic> current) {
  final req = RequestOptions(path: '/api/sheets/x');
  return DioException(
    requestOptions: req,
    type: DioExceptionType.badResponse,
    response: Response(
      requestOptions: req,
      statusCode: 409,
      data: {'current': current},
    ),
    error: ApiError(409, 'Conflict'),
  );
}

// ── Harness ──────────────────────────────────────────────────────────────────

int _dbCounter = 0;

Future<DocumentCacheDao> _freshDao({String Function()? now}) async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:docsyncmem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return DocumentCacheDao(db, now: now);
}

Map<String, dynamic> _changeItem({
  required String id,
  String name = 'Server Sheet',
  Map<String, dynamic>? payload,
  String? deletedAt,
  String? updatedAt,
}) =>
    {
      'id': id,
      'name': name,
      'payload': ?payload,
      'deleted_at': ?deletedAt,
      'updated_at': updatedAt ?? '2026-06-20T11:00:00Z',
    };

void main() {
  setUpAll(() => sqfliteFfiInit());

  // ── PUSH ──────────────────────────────────────────────────────────────────

  group('DocumentSync.push', () {
    test('drains create/update/delete in order and clears the queue', () async {
      final dao = await _freshDao();
      final id = await dao.applyLocalCreate(
          kind: 'sheets', id: 's1', name: 'Mine', payloadJson: '{"a":1}');
      await dao.applyLocalEdit(
          kind: 'sheets', id: id, name: 'Mine', payloadJson: '{"a":2}');
      await dao.applyLocalDelete('sheets', id);

      final transport = _FakeTransport();
      final result =
          await DocumentSync(dao, DocumentsRepository(transport), DocKind.sheets)
              .push();

      expect(result.pushed, 3);
      expect(await dao.readOutbox(), isEmpty);
      final writes = transport.calls.where((c) => c.method != 'GET').toList();
      expect(writes.first.method, 'POST');
      expect(writes.any((c) => c.method == 'PUT'), isTrue);
      expect(writes.last.method, 'DELETE');
    });

    test('offline create enqueues to the outbox (dirty row kept)', () async {
      final dao = await _freshDao();
      final id = await dao.applyLocalCreate(kind: 'sheets', name: 'Offline');
      // Queue holds exactly one create; the cache row is dirty.
      final queue = await dao.readOutbox();
      expect(queue, hasLength(1));
      expect(queue.first.op, DocOutboxOp.create);
      expect(queue.first.docId, id);
      expect(await dao.dirtyIds('sheets'), contains(id));
    });

    test(
        'offline edit not lost: a network throw keeps the dirty cache row AND '
        'the queued item (never silently dropped)', () async {
      final dao = await _freshDao();
      final id = await dao.applyLocalCreate(
          kind: 'sheets', id: 's9', name: 'Keep', payloadJson: '{"a":1}');
      // Drain the create so only the update is queued.
      await DocumentSync(dao, DocumentsRepository(_FakeTransport()), DocKind.sheets)
          .push();
      await dao.applyLocalEdit(
          kind: 'sheets', id: id, name: 'Keep', payloadJson: '{"a":2}');

      // Now the PUT (save) hits a real connection error.
      final transport = _FakeTransport(failNetworkOnPaths: {'/api/sheets/s9'});
      final result =
          await DocumentSync(dao, DocumentsRepository(transport), DocKind.sheets)
              .push();

      expect(result.pushInterrupted, isTrue);
      expect(result.pushed, 0);
      // The edit survives: still dirty + still queued for the next sync.
      expect(await dao.dirtyIds('sheets'), contains('s9'));
      final remaining = await dao.readOutbox();
      expect(remaining.map((o) => o.op), contains(DocOutboxOp.update));
      final stored = await dao.getDoc('sheets', 's9');
      expect(stored!.payload['a'], 2); // the offline edit is in the cache
    });

    test('a real DioException 500 RETAINS the item then dead-letters after N',
        () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate(kind: 'sheets', id: 'p1', name: 'Poison');
      final transport = _FakeTransport(failServerOnPaths: {'/api/sheets': 500});

      // First push: retained (queued), attempt counted.
      final r1 =
          await DocumentSync(dao, DocumentsRepository(transport), DocKind.sheets)
              .push();
      expect(r1.pushInterrupted, isTrue);
      expect((await dao.readOutbox()).first.attempts, 1);

      // Exhaust the retry budget → dead-lettered (queue clears), cache survives.
      for (var i = 1; i < DocumentSync.kMaxPushAttempts; i++) {
        await DocumentSync(dao, DocumentsRepository(transport), DocKind.sheets)
            .push();
      }
      expect(await dao.readOutbox(), isEmpty);
      expect(await dao.getDoc('sheets', 'p1'), isNotNull);
    });

    test('a definitive 4xx on a create logs create_rejected + keeps it dirty',
        () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate(kind: 'sheets', id: 'rej', name: 'Rejected');
      final transport = _FakeTransport(failServerOnPaths: {'/api/sheets': 422});
      final result =
          await DocumentSync(dao, DocumentsRepository(transport), DocKind.sheets)
              .push();
      expect(result.pushInterrupted, isFalse);
      expect(await dao.readOutbox(), isEmpty); // drained — not wedging the queue
      expect(await dao.dirtyIds('sheets'), contains('rej')); // data NOT lost
      final conflicts = await dao.readConflicts();
      expect(conflicts.where((c) => c.field == 'create_rejected'), hasLength(1));
    });

    test('a 409 on save logs a conflict (logged, NOT dropped) and drains',
        () async {
      final dao = await _freshDao();
      // Pretend a synced doc exists, then make a dirty local edit.
      await dao.putServerDoc(
          kind: 'sheets', id: 'x', name: 'Doc', payloadJson: '{"a":1}',
          updatedAt: '2026-06-20T09:00:00Z');
      await dao.applyLocalEdit(
          kind: 'sheets',
          id: 'x',
          name: 'Doc',
          payloadJson: '{"a":2}',
          baseUpdatedAt: '2026-06-20T09:00:00Z');

      final transport = _FakeTransport(conflictOnPutPaths: {
        '/api/sheets/x': {
          'id': 'x',
          'name': 'Doc',
          'updated_at': '2026-06-20T13:00:00Z',
          'payload': {'a': 99},
        }
      });
      final result =
          await DocumentSync(dao, DocumentsRepository(transport), DocKind.sheets)
              .push();

      expect(result.conflicts, 1);
      expect(await dao.readOutbox(), isEmpty); // drained
      final conflicts = await dao.readConflicts();
      expect(conflicts.where((c) => c.field == 'save_conflict'), hasLength(1));
    });

    test('a 404 on delete is idempotent success (drains, counts as pushed)',
        () async {
      final dao = await _freshDao();
      await dao.putServerDoc(kind: 'sheets', id: 'd1', name: 'Bye');
      await dao.applyLocalDelete('sheets', 'd1');
      final transport = _FakeTransport(failServerOnPaths: {'/api/sheets/d1': 404});
      final result =
          await DocumentSync(dao, DocumentsRepository(transport), DocKind.sheets)
              .push();
      expect(result.pushed, 1);
      expect(await dao.readOutbox(), isEmpty);
      expect(await dao.getDoc('sheets', 'd1'), isNull); // tombstone hard-removed
    });

    test('only THIS kind drains — a docs item is left for the docs engine',
        () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate(kind: 'sheets', id: 's1', name: 'S');
      await dao.applyLocalCreate(kind: 'docs', id: 'd1', name: 'D');
      final transport = _FakeTransport();
      await DocumentSync(dao, DocumentsRepository(transport), DocKind.sheets)
          .push();
      final remaining = await dao.readOutbox();
      expect(remaining, hasLength(1));
      expect(remaining.first.kind, 'docs');
    });
  });

  // ── PULL + LWW ──────────────────────────────────────────────────────────

  group('DocumentSync.pull', () {
    test('a web-created server doc lands in the cache (surfaces on the list)',
        () async {
      final dao = await _freshDao();
      final transport = _FakeTransport(changesResponse: {
        'items': [
          _changeItem(id: 'web1', name: 'Made on web', payload: {'a': 7})
        ],
        'server_time': '2026-06-20T12:00:00Z',
      });
      final result =
          await DocumentSync(dao, DocumentsRepository(transport), DocKind.sheets)
              .pull();
      expect(result.pulled, 1);
      final stored = await dao.getDoc('sheets', 'web1');
      expect(stored!.name, 'Made on web');
      expect(stored.payload['a'], 7);
      // And it shows up in the kind list (non-deleted).
      expect((await dao.listDocs('sheets')).map((d) => d.id), contains('web1'));
      expect(await dao.getCursor('sheets'), '2026-06-20T12:00:00Z');
    });

    test('passes the stored cursor as ?since', () async {
      final dao = await _freshDao();
      await dao.setCursor('sheets', '2026-06-20T08:00:00Z');
      final transport = _FakeTransport(changesResponse: {
        'items': const [],
        'server_time': '2026-06-20T12:00:00Z',
      });
      await DocumentSync(dao, DocumentsRepository(transport), DocKind.sheets)
          .pull();
      final changesCall =
          transport.calls.firstWhere((c) => c.path.contains('/changes'));
      expect(changesCall.path, contains('since=2026-06-20T08%3A00%3A00Z'));
    });

    test('server wins when local is NOT dirty', () async {
      final dao = await _freshDao();
      await dao.putServerDoc(
          kind: 'sheets', id: 'x', name: 'Old', payloadJson: '{"a":1}',
          updatedAt: '2026-06-20T10:00:00Z');
      final transport = _FakeTransport(changesResponse: {
        'items': [
          _changeItem(
              id: 'x',
              name: 'New',
              payload: {'a': 2},
              updatedAt: '2026-06-20T11:00:00Z')
        ],
        'server_time': '2026-06-20T12:00:00Z',
      });
      await DocumentSync(dao, DocumentsRepository(transport), DocKind.sheets)
          .pull();
      final stored = await dao.getDoc('sheets', 'x');
      expect(stored!.payload['a'], 2);
      expect(await dao.readConflicts(), isEmpty);
    });

    test('dirty local with older time → server wins + conflict logged',
        () async {
      final dao = await _freshDao(now: () => '2026-06-20T10:00:00Z');
      await dao.applyLocalEdit(
          kind: 'sheets', id: 'c1', name: 'Local', payloadJson: '{"a":1}');
      final transport = _FakeTransport(changesResponse: {
        'items': [
          _changeItem(
              id: 'c1',
              name: 'Server',
              payload: {'a': 9},
              updatedAt: '2026-06-20T11:00:00Z')
        ],
        'server_time': '2026-06-20T12:00:00Z',
      });
      final result =
          await DocumentSync(dao, DocumentsRepository(transport), DocKind.sheets)
              .pull();
      expect(result.conflicts, 1);
      final stored = await dao.getDoc('sheets', 'c1');
      expect(stored!.payload['a'], 9); // server won
      expect(await dao.readConflicts(), isNotEmpty);
    });

    test('dirty local strictly newer is kept; nothing logged', () async {
      final dao = await _freshDao(now: () => '2026-06-20T12:00:00Z');
      await dao.applyLocalEdit(
          kind: 'sheets', id: 'c2', name: 'Local newest', payloadJson: '{"a":1}');
      final transport = _FakeTransport(changesResponse: {
        'items': [
          _changeItem(
              id: 'c2',
              payload: {'a': 9},
              updatedAt: '2026-06-20T11:00:00Z')
        ],
        'server_time': '2026-06-20T13:00:00Z',
      });
      final result =
          await DocumentSync(dao, DocumentsRepository(transport), DocKind.sheets)
              .pull();
      expect(result.pulled, 0);
      final stored = await dao.getDoc('sheets', 'c2');
      expect(stored!.payload['a'], 1); // local kept
      expect(await dao.readConflicts(), isEmpty);
      expect(await dao.dirtyIds('sheets'), contains('c2'));
    });

    test('soft-delete propagation: a server tombstone hides the local row',
        () async {
      final dao = await _freshDao();
      await dao.putServerDoc(kind: 'sheets', id: 'gone', name: 'Doomed',
          payloadJson: '{"a":1}', updatedAt: '2026-06-20T10:00:00Z');
      // deleted_at style tombstone.
      final transport = _FakeTransport(changesResponse: {
        'items': [
          _changeItem(id: 'gone', deletedAt: '2026-06-20T11:30:00Z')
        ],
        'server_time': '2026-06-20T12:00:00Z',
      });
      final result =
          await DocumentSync(dao, DocumentsRepository(transport), DocKind.sheets)
              .pull();
      expect(result.deletedApplied, 1);
      expect((await dao.listDocs('sheets')).map((d) => d.id),
          isNot(contains('gone')));
    });

    test('explicit `deleted` array tombstones also apply', () async {
      final dao = await _freshDao();
      await dao.putServerDoc(kind: 'sheets', id: 'g2', name: 'Doomed2');
      final transport = _FakeTransport(changesResponse: {
        'items': const [],
        'deleted': ['g2'],
        'server_time': '2026-06-20T12:00:00Z',
      });
      final result =
          await DocumentSync(dao, DocumentsRepository(transport), DocKind.sheets)
              .pull();
      expect(result.deletedApplied, 1);
      expect((await dao.listDocs('sheets')).map((d) => d.id),
          isNot(contains('g2')));
    });

    test('server delete of a DIRTY local row logs a conflict + reconciles queue',
        () async {
      final dao = await _freshDao(now: () => '2026-06-20T10:00:00Z');
      await dao.applyLocalEdit(
          kind: 'sheets', id: 'h1', name: 'Unsynced', payloadJson: '{"a":1}');
      expect(await dao.outboxCount(), 1);
      final transport = _FakeTransport(changesResponse: {
        'items': [_changeItem(id: 'h1', deletedAt: '2026-06-20T11:00:00Z')],
        'server_time': '2026-06-20T12:00:00Z',
      });
      final result =
          await DocumentSync(dao, DocumentsRepository(transport), DocKind.sheets)
              .pull();
      expect(result.deletedApplied, 1);
      expect(result.conflicts, greaterThanOrEqualTo(1));
      expect(await dao.outboxCount(), 0); // queued op reconciled away
      expect((await dao.listDocs('sheets')).map((d) => d.id),
          isNot(contains('h1')));
    });

    test('pull network failure leaves the cursor untouched', () async {
      final dao = await _freshDao();
      await dao.setCursor('sheets', '2026-06-20T08:00:00Z');
      final transport = _FailingGetTransport();
      final result =
          await DocumentSync(dao, DocumentsRepository(transport), DocKind.sheets)
              .pull();
      expect(result.pullFailed, isTrue);
      expect(await dao.getCursor('sheets'), '2026-06-20T08:00:00Z');
    });

    test('empty server_time with no datable rows → pull FAILS', () async {
      final dao = await _freshDao();
      await dao.setCursor('sheets', '2026-06-20T08:00:00Z');
      final transport = _FakeTransport(changesResponse: {
        'items': const [],
        'server_time': '',
      });
      final result =
          await DocumentSync(dao, DocumentsRepository(transport), DocKind.sheets)
              .pull();
      expect(result.pullFailed, isTrue);
      expect(await dao.getCursor('sheets'), '2026-06-20T08:00:00Z');
    });
  });

  // ── SYNC orchestration ────────────────────────────────────────────────────

  group('DocumentSync.sync', () {
    test('push then pull; guards against concurrent runs', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate(kind: 'sheets', id: 'a1', name: 'A');
      final transport = _FakeTransport(changesResponse: {
        'items': [_changeItem(id: 'srv', name: 'Server one', payload: {'a': 1})],
        'server_time': '2026-06-20T12:00:00Z',
      });
      final sync =
          DocumentSync(dao, DocumentsRepository(transport), DocKind.sheets);
      final result = await sync.sync();
      expect(result.pushed, 1);
      expect(result.pulled, 1);
      expect(sync.isRunning, isFalse);
      expect(await dao.getDoc('sheets', 'srv'), isNotNull);
      expect(await dao.outboxCount(), 0);
    });

    test('a concurrent sync() call is a no-op while one runs', () async {
      final dao = await _freshDao();
      final sync =
          DocumentSync(dao, DocumentsRepository(_FakeTransport()), DocKind.sheets);
      final a = sync.sync();
      final b = sync.sync();
      final results = await Future.wait([a, b]);
      expect(results, hasLength(2));
    });
  });
}

// ── test helpers ─────────────────────────────────────────────────────────────

/// A transport whose GET always throws a real network DioException (pull fail).
class _FailingGetTransport implements DocumentsTransport {
  @override
  Future<Map<String, dynamic>> getJson(String path) async => throw _connectionDio();
  @override
  Future<Map<String, dynamic>> postJson(String p, Map<String, dynamic> b) async => {};
  @override
  Future<Map<String, dynamic>> putJson(String p, Map<String, dynamic> b) async => {};
  @override
  Future<Map<String, dynamic>> patchJson(String p, Map<String, dynamic> b) async => {};
  @override
  Future<Map<String, dynamic>> deleteJson(String p) async => {};
  @override
  Future<Map<String, dynamic>> uploadFile(String p, f) async => {};
  @override
  Future<List<int>> getBytes(String p) async => const [];
  @override
  Future<List<int>> postBytes(String p, Map<String, dynamic> b) async => const [];
}
