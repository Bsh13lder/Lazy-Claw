import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/budgets_dao.dart';
import 'package:lazyclaw_mobile/models/expense.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/repositories/budgets_repository.dart';
import 'package:lazyclaw_mobile/sync/budgets_sync.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

// ── Programmable fake transport ──────────────────────────────────────────────

class _Call {
  final String method;
  final String path;
  final Map<String, dynamic>? body;
  final Map<String, dynamic>? query;
  _Call(this.method, this.path, {this.body, this.query});
}

/// Records every call. `changesResponse` is returned by GET /api/budgets/changes;
/// the various fail maps inject errors on matching write paths.
class _FakeTransport implements BudgetsTransport {
  final List<_Call> calls = [];
  Map<String, dynamic> changesResponse;

  /// Substring → throw network ApiError(0) when a write path contains it.
  final Set<String> failNetworkOnPaths;

  /// Substring → throw a non-network ApiError(status) on the write path.
  final Map<String, int> failServerOnPaths;

  /// Substring → throw a real production-shaped [DioException] on the write
  /// path (the `ApiError` lives in `DioException.error`).
  final Map<String, DioException Function()> dioErrorOnPaths;

  _FakeTransport({
    Map<String, dynamic>? changesResponse,
    Set<String>? failNetworkOnPaths,
    Map<String, int>? failServerOnPaths,
    Map<String, DioException Function()>? dioErrorOnPaths,
  })  : changesResponse = changesResponse ??
            {
              'projects': [],
              'expenses': [],
              'deleted_projects': [],
              'deleted_expenses': [],
              'now': '',
            },
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
    return {'projects': [], 'expenses': []};
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

Future<BudgetsDao> _freshDao({String Function()? now}) async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:budgetsyncmem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return BudgetsDao(db, now: now);
}

Map<String, dynamic> _serverProjectJson({
  String id = 'sp1',
  String name = 'Server project',
  double budget = 1000.0,
  String status = 'active',
  String? updatedAt,
  String createdAt = '2026-06-05T10:00:00Z',
}) =>
    {
      'id': id,
      'name': name,
      'budget': budget,
      'currency': 'USD',
      'status': status,
      'spent': 0.0,
      'remaining': budget,
      'created_at': createdAt,
      'updated_at': updatedAt ?? createdAt,
    };

Map<String, dynamic> _serverExpenseJson({
  String id = 'se1',
  String projectId = 'sp1',
  double amount = 50.0,
  String description = 'Server expense',
  String? updatedAt,
  String createdAt = '2026-06-05T10:00:00Z',
}) =>
    {
      'id': id,
      'project_id': projectId,
      'amount': amount,
      'currency': 'USD',
      'description': description,
      'status': 'posted',
      'created_at': createdAt,
      'updated_at': updatedAt ?? createdAt,
    };

/// A production-shaped server-error DioException — a real HTTP response with a
/// status code, carrying the `ApiError` in `.error` exactly like the app's
/// `_ErrorInterceptor` rethrows it. THE key regression guard.
DioException _serverDio(int status) {
  final req = RequestOptions(path: '/api/budgets/projects');
  return DioException(
    requestOptions: req,
    type: DioExceptionType.badResponse,
    response: Response(requestOptions: req, statusCode: status),
    error: ApiError(status, 'Server $status'),
  );
}

/// A connection-type DioException (no response reached us) — the network-down
/// shape, with `ApiError(0)` in `.error` like the interceptor would produce.
DioException _connectionDio() {
  final req = RequestOptions(path: '/api/budgets/projects');
  return DioException(
    requestOptions: req,
    type: DioExceptionType.connectionError,
    error: ApiError(0, 'Network error'),
  );
}

void main() {
  setUpAll(() => sqfliteFfiInit());

  // ── PUSH ──────────────────────────────────────────────────────────────────

  group('BudgetsSync.sync self-heal', () {
    test('re-pushes a stranded orphan create (reserva-1000 recovery)', () async {
      final dao = await _freshDao();
      await dao.applyLocalExpenseCreate('p1', 1000.0, 'reserva',
          id: 'exp-reserva', currency: 'EUR');
      // Simulate the create op being dropped (dead-lettered / old silent drain)
      // — the expense is now a dirty orphan: on-device but never on the server.
      for (final o in await dao.readBudgetsOutbox()) {
        await dao.deleteOutboxItem(o.seq);
      }
      expect(await dao.readBudgetsOutbox(), isEmpty);
      expect(await dao.dirtyExpenseIds(), contains('exp-reserva'));

      final transport = _FakeTransport();
      final result = await BudgetsSync(dao, BudgetsRepository(transport)).sync();

      // sync() healed the orphan and pushed it to the server.
      final posts = transport.calls
          .where((c) => c.method == 'POST' && c.path.contains('/expenses'))
          .toList();
      expect(posts.length, 1);
      expect(posts.first.body?['id'], 'exp-reserva');
      expect((posts.first.body?['amount'] as num).toDouble(), 1000.0);
      expect(result.pushed, 1);
      expect(await dao.readBudgetsOutbox(), isEmpty);
      expect(await dao.dirtyExpenseIds(), isNot(contains('exp-reserva')));
    });
  });

  group('BudgetsSync.push', () {
    test('drains both entities in seq order and clears the outbox', () async {
      final dao = await _freshDao();
      await dao.applyLocalProjectCreate('Proj', id: 'p1');
      await dao.applyLocalExpenseCreate('p1', 25.0, 'Lunch', id: 'e1');
      await dao.applyLocalProjectUpdate('p1', name: 'Proj2');

      final transport = _FakeTransport();
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).push();

      expect(result.pushed, 3);
      expect(await dao.readBudgetsOutbox(), isEmpty);

      final writes = transport.calls.where((c) => c.method != 'GET').toList();
      expect(writes[0].method, 'POST');
      expect(writes[0].path, '/api/budgets/projects');
      expect(writes[0].body!['id'], 'p1');
      // Expense create routes to /projects/{id}/expenses with the client id.
      expect(writes[1].method, 'POST');
      expect(writes[1].path, '/api/budgets/projects/p1/expenses');
      expect(writes[1].body!['id'], 'e1');
      expect(writes[1].body!['amount'], 25.0);
      // Project update routes to PATCH /projects/{id} with id stripped.
      expect(writes[2].method, 'PATCH');
      expect(writes[2].path, '/api/budgets/projects/p1');
      expect(writes[2].body!.containsKey('id'), isFalse);
      expect(writes[2].body!['name'], 'Proj2');
    });

    test(
        'H4: an expense create pushes currency + spent_at (no fidelity loss) so '
        'the server + web render the right money + date', () async {
      final dao = await _freshDao(now: () => '2026-07-01T10:00:00Z');
      // A EUR expense (the reserva) — currency must not be silently dropped to
      // the server default, or the web renders it as the wrong currency.
      await dao.applyLocalExpenseCreate('projE', 1000.0, 'Reserva',
          id: 'eurexp', currency: 'EUR');
      final transport = _FakeTransport();
      await BudgetsSync(dao, BudgetsRepository(transport)).push();
      final call = transport.calls
          .firstWhere((c) => c.path.contains('/projects/projE/expenses'));
      expect(call.body!['currency'], 'EUR');
      expect(call.body!['spent_at'], '2026-07-01T10:00:00Z');
      expect(call.body!['amount'], 1000.0);
      expect(call.body!['id'], 'eurexp');
    });

    test('project create replays the client id (idempotent)', () async {
      final dao = await _freshDao();
      await dao.applyLocalProjectCreate('Pinned', id: 'fixed-proj');
      final transport = _FakeTransport();
      await BudgetsSync(dao, BudgetsRepository(transport)).push();
      final call = transport.calls
          .firstWhere((c) => c.path == '/api/budgets/projects');
      expect(call.body!['id'], 'fixed-proj');
    });

    test('expense create replays the client id + project path', () async {
      final dao = await _freshDao();
      await dao.applyLocalExpenseCreate('projX', 9.0, 'X', id: 'fixed-exp');
      final transport = _FakeTransport();
      await BudgetsSync(dao, BudgetsRepository(transport)).push();
      final call = transport.calls.firstWhere(
          (c) => c.path == '/api/budgets/projects/projX/expenses');
      expect(call.body!['id'], 'fixed-exp');
    });

    test('a delete that pushed hard-removes the project tombstone', () async {
      final dao = await _freshDao();
      await dao.applyLocalProjectCreate('Bye', id: 'd1');
      await BudgetsSync(dao, BudgetsRepository(_FakeTransport())).push();
      await dao.applyLocalProjectDelete('d1');

      await BudgetsSync(dao, BudgetsRepository(_FakeTransport())).push();
      expect(await dao.getProject('d1'), isNull);
      expect(await dao.readBudgetsOutbox(), isEmpty);
    });

    test('a delete that pushed hard-removes the expense tombstone', () async {
      final dao = await _freshDao();
      await dao.applyLocalExpenseCreate('p1', 5.0, 'Bye', id: 'ed1');
      await BudgetsSync(dao, BudgetsRepository(_FakeTransport())).push();
      await dao.applyLocalExpenseDelete('ed1');

      await BudgetsSync(dao, BudgetsRepository(_FakeTransport())).push();
      expect(await dao.getExpense('ed1'), isNull);
      expect(await dao.readBudgetsOutbox(), isEmpty);
    });

    test('stops at the first network failure and keeps the rest queued',
        () async {
      final dao = await _freshDao();
      await dao.applyLocalProjectCreate('First', id: 'f1'); // POST ok
      await dao.applyLocalExpenseCreate('f1', 5.0, 'second', id: 'e2'); // fails

      final transport = _FakeTransport(
        // The expense create POSTs to /projects/f1/expenses.
        failNetworkOnPaths: {'/expenses'},
      );
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).push();

      expect(result.pushInterrupted, isTrue);
      final remaining = await dao.readBudgetsOutbox();
      // The project create drained; the failing expense create stays.
      expect(remaining.map((o) => o.entity), contains(kExpenseEntity));
      expect(remaining.every((o) => o.entity != kProjectEntity), isTrue);
    });

    test('non-network server error dequeues (does not wedge the queue)',
        () async {
      final dao = await _freshDao();
      await dao.applyLocalProjectCreate('A', id: 'a1');
      await dao.applyLocalProjectCreate('B', id: 'b1');
      final transport = _FakeTransport(
        failServerOnPaths: {'/api/budgets/projects': 422},
      );
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).push();
      expect(result.pushInterrupted, isFalse);
      expect(await dao.readBudgetsOutbox(), isEmpty);
    });

    // ── Production DioException error-classification regression guards ──

    test(
        'a real DioException(badResponse, error: ApiError(500)) RETAINS the '
        'outbox item (never silently dropped)', () async {
      final dao = await _freshDao();
      await dao.applyLocalProjectCreate('Keep me', id: 'srv5xx');
      final transport = _FakeTransport(
        dioErrorOnPaths: {'/api/budgets/projects': () => _serverDio(500)},
      );
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).push();

      expect(result.pushInterrupted, isTrue);
      expect(result.pushed, 0);
      final remaining = await dao.readBudgetsOutbox();
      expect(remaining, hasLength(1));
      expect(remaining.first.op, BudgetsOutboxOp.create);
      expect(remaining.first.attempts, 1); // counted for eventual dead-letter
    });

    test(
        'a connection-type DioException stops the drain and preserves the '
        'whole queue', () async {
      final dao = await _freshDao();
      await dao.applyLocalProjectCreate('First', id: 'n1'); // POST ok
      await dao.applyLocalExpenseCreate('n1', 3.0, 'second', id: 'n2'); // drop

      final transport = _FakeTransport(
        dioErrorOnPaths: {'/expenses': () => _connectionDio()},
      );
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).push();

      expect(result.pushInterrupted, isTrue);
      final remaining = await dao.readBudgetsOutbox();
      expect(remaining.map((o) => o.entity), contains(kExpenseEntity));
      expect(remaining.every((o) => o.entity != kProjectEntity), isTrue);
      // No attempt bumped for a network error (only 5xx counts).
      expect(
          remaining.firstWhere((o) => o.entity == kExpenseEntity).attempts, 0);
    });

    test('a real DioException 404 on delete is treated as success (drains)',
        () async {
      final dao = await _freshDao();
      await dao.applyLocalProjectCreate('Bye', id: 'gone404');
      await BudgetsSync(dao, BudgetsRepository(_FakeTransport())).push();
      await dao.applyLocalProjectDelete('gone404');

      final transport = _FakeTransport(
        dioErrorOnPaths: {'/api/budgets/projects/gone404': () => _serverDio(404)},
      );
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).push();
      expect(result.pushInterrupted, isFalse);
      expect(result.pushed, 1);
      expect(await dao.readBudgetsOutbox(), isEmpty);
      expect(await dao.getProject('gone404'), isNull); // tombstone removed
    });

    test('a real DioException 422 (client error) drains the item', () async {
      final dao = await _freshDao();
      await dao.applyLocalProjectCreate('Bad', id: 'bad422');
      final transport = _FakeTransport(
        dioErrorOnPaths: {'/api/budgets/projects': () => _serverDio(422)},
      );
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).push();
      expect(result.pushInterrupted, isFalse);
      expect(await dao.readBudgetsOutbox(), isEmpty);
    });

    test(
        'H2: an EXPENSE create that 4xxes does not vanish — a "create rejected" '
        'conflict is logged (cache row stays dirty for the UI)', () async {
      final dao = await _freshDao();
      await dao.applyLocalExpenseCreate('projZ', 17.0, 'Rejected lunch',
          id: 'rej-exp');
      final transport = _FakeTransport(
        // The expense create POSTs to /projects/projZ/expenses.
        dioErrorOnPaths: {'/expenses': () => _serverDio(422)},
      );
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).push();

      // Drained from the outbox (no infinite retry) but NOT silently — a
      // conflict surfaces the rejection, and the dirty cache row stays put.
      expect(result.pushInterrupted, isFalse);
      expect(result.pushed, 0);
      expect(await dao.readBudgetsOutbox(), isEmpty);

      final conflicts = await dao.readConflicts();
      final rejected =
          conflicts.where((c) => c.field == 'create_rejected').toList();
      expect(rejected, hasLength(1));
      expect(rejected.single.id, 'rej-exp');
      expect(rejected.single.local, contains('422'));
      expect(rejected.single.server, isNull);

      // The cache row is the only remaining record of the user's intent — it
      // must still be present + dirty so the UI can show it as un-synced.
      expect(await dao.getExpense('rej-exp'), isNotNull);
      expect(await dao.dirtyExpenseIds(), contains('rej-exp'));
    });

    test(
        'H2: a PROJECT create that 4xxes also logs a "create rejected" conflict',
        () async {
      final dao = await _freshDao();
      await dao.applyLocalProjectCreate('Rejected project', id: 'rej-proj');
      final transport = _FakeTransport(
        dioErrorOnPaths: {'/api/budgets/projects': () => _serverDio(400)},
      );
      await BudgetsSync(dao, BudgetsRepository(transport)).push();

      final rejected = (await dao.readConflicts())
          .where((c) => c.field == 'create_rejected')
          .toList();
      expect(rejected, hasLength(1));
      expect(rejected.single.id, 'rej-proj');
      expect(await dao.dirtyProjectIds(), contains('rej-proj'));
    });

    test(
        'H2: a definitive 4xx on an UPDATE drains silently (no create_rejected '
        'conflict — the next pull restores server truth)', () async {
      final dao = await _freshDao();
      // Commit the create first so the row is clean + server-known.
      await dao.applyLocalProjectCreate('Exists', id: 'upd1');
      await BudgetsSync(dao, BudgetsRepository(_FakeTransport())).push();
      // Now a local update that the server rejects with a 4xx.
      await dao.applyLocalProjectUpdate('upd1', name: 'New name');
      final transport = _FakeTransport(
        dioErrorOnPaths: {'/api/budgets/projects/upd1': () => _serverDio(422)},
      );
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).push();

      expect(result.pushInterrupted, isFalse);
      expect(await dao.readBudgetsOutbox(), isEmpty);
      // No create_rejected conflict for an update — drain is the right behavior.
      expect(
          (await dao.readConflicts())
              .where((c) => c.field == 'create_rejected'),
          isEmpty);
    });

    test(
        'H3: a 404 on an EXPENSE create (parent project not on the server yet) '
        'is RETRYABLE — kept queued + attempts bumped, NOT stranded on the first '
        'failure (the reserva-1000 data-loss bug)', () async {
      final dao = await _freshDao();
      // A child expense whose parent project has not synced to the server yet:
      // the create route 404s (store.create_expense can\'t find the project).
      await dao.applyLocalExpenseCreate('unsyncedProj', 1000.0, 'Reserva',
          id: 'reserva1');
      final transport = _FakeTransport(
        dioErrorOnPaths: {'/expenses': () => _serverDio(404)},
      );
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).push();

      // A 404-parent-not-found is transient (the project may still sync), so the
      // child create is KEPT queued for the next sync — never dropped on the
      // first failure the way a 400/422 validation reject is.
      expect(result.pushInterrupted, isTrue);
      expect(result.pushed, 0);
      final remaining = await dao.readBudgetsOutbox();
      expect(remaining, hasLength(1));
      expect(remaining.single.entityId, 'reserva1');
      expect(remaining.single.attempts, 1); // counted toward eventual dead-letter
      // Not stranded, and no premature create_rejected while it can still recover.
      expect(
          (await dao.readConflicts())
              .where((c) => c.field == 'create_rejected'),
          isEmpty);
    });

    test(
        'H3: an expense create that 404s past kMaxPushAttempts dead-letters '
        '(bounded) — cache stays dirty + one create_rejected conflict is logged',
        () async {
      final dao = await _freshDao();
      await dao.applyLocalExpenseCreate('unsyncedProj', 1000.0, 'Reserva',
          id: 'reserva2');
      final transport = _FakeTransport(
        dioErrorOnPaths: {'/expenses': () => _serverDio(404)},
      );
      for (var i = 0; i < BudgetsSync.kMaxPushAttempts; i++) {
        await BudgetsSync(dao, BudgetsRepository(transport)).push();
      }
      // Bounded: the poison child stops retrying, but is NEVER silently lost.
      expect(await dao.readBudgetsOutbox(), isEmpty);
      expect(await dao.dirtyExpenseIds(), contains('reserva2'));
      final rejected = (await dao.readConflicts())
          .where((c) => c.field == 'create_rejected')
          .toList();
      expect(rejected, hasLength(1));
      expect(rejected.single.id, 'reserva2');
      expect(rejected.single.local, contains('404'));
    });

    test('a 5xx item dead-letters after kMaxPushAttempts, never wedges',
        () async {
      final dao = await _freshDao();
      await dao.applyLocalProjectCreate('Poison', id: 'p1');
      final transport = _FakeTransport(
        dioErrorOnPaths: {'/api/budgets/projects': () => _serverDio(503)},
      );
      for (var i = 0; i < BudgetsSync.kMaxPushAttempts; i++) {
        await BudgetsSync(dao, BudgetsRepository(transport)).push();
      }
      expect(await dao.readBudgetsOutbox(), isEmpty);
      // Cache row stays dirty so the next pull re-establishes server truth.
      expect(await dao.dirtyProjectIds(), contains('p1'));
    });

    test(
        'M: multiple pending PROJECT updates coalesce into ONE PATCH carrying '
        'the client updated_at (later values win)', () async {
      final dao = await _freshDao();
      // Create then commit so the row is clean (isolate the update coalescing).
      await dao.applyLocalProjectCreate('Orig', id: 'co1');
      await BudgetsSync(dao, BudgetsRepository(_FakeTransport())).push();

      // Two consecutive local edits → two queued update rows.
      await dao.applyLocalProjectUpdate('co1', name: 'Edit one');
      await dao.applyLocalProjectUpdate('co1', name: 'Edit two', budget: 200.0);
      expect(await dao.outboxCount(), 2);

      final transport = _FakeTransport();
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).push();

      // Both outbox rows retire, but only ONE PATCH hits the network.
      expect(await dao.readBudgetsOutbox(), isEmpty);
      final patches = transport.calls
          .where((c) => c.method == 'PATCH' && c.path.contains('/co1'))
          .toList();
      expect(patches, hasLength(1));
      // Later values win, both folded fields present.
      expect(patches.single.body!['name'], 'Edit two');
      expect(patches.single.body!['budget'], 200.0);
      // Client updated_at is stamped on the merged PATCH so a self-stamping
      // server can't win LWW and revert this edit.
      expect(patches.single.body!.containsKey('updated_at'), isTrue);
      expect(patches.single.body!.containsKey('id'), isFalse);
      expect(result.pushed, greaterThanOrEqualTo(1));
    });

    test(
        'an expense whose ONLY dirty change is is_favorite pushes a PATCH '
        'carrying is_favorite (real bool) to /api/budgets/expenses/{id}',
        () async {
      final dao = await _freshDao();
      // Create + commit so the row is clean and server-known first.
      await dao.applyLocalExpenseCreate('projF', 5.0, 'Latte', id: 'favexp');
      await BudgetsSync(dao, BudgetsRepository(_FakeTransport())).push();

      // The only pending change is the star flip.
      await dao.applyLocalExpenseFavorite('favexp', true);

      final transport = _FakeTransport();
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).push();

      expect(result.pushed, 1);
      expect(await dao.readBudgetsOutbox(), isEmpty);
      final patches = transport.calls
          .where((c) =>
              c.method == 'PATCH' &&
              c.path == '/api/budgets/expenses/favexp')
          .toList();
      expect(patches, hasLength(1));
      expect(patches.single.body!['is_favorite'], isTrue);
      // The id rides in the URL, never the PATCH body.
      expect(patches.single.body!.containsKey('id'), isFalse);
    });

    test('commitPush is idempotent — replaying a retired item is a no-op',
        () async {
      final dao = await _freshDao();
      final p = await dao.applyLocalProjectCreate('A', id: 'c2a');
      final seq = (await dao.readBudgetsOutbox()).first.seq;
      await dao.commitPush(seq, kProjectEntity, p.id);
      expect(await dao.readBudgetsOutbox(), isEmpty);
      expect(await dao.dirtyProjectIds(), isEmpty);
      await dao.commitPush(seq, kProjectEntity, p.id);
      expect(await dao.dirtyProjectIds(), isEmpty);
      expect(await dao.getProject(p.id), isNotNull);
    });
  });

  // ── PULL + LWW ──────────────────────────────────────────────────────────

  group('BudgetsSync.pull last-write-wins', () {
    test('writes brand-new server project + expense into the cache', () async {
      final dao = await _freshDao();
      final transport = _FakeTransport(changesResponse: {
        'projects': [_serverProjectJson(id: 'sp', name: 'Hello')],
        'expenses': [_serverExpenseJson(id: 'se', projectId: 'sp')],
        'deleted_projects': [],
        'deleted_expenses': [],
        'now': '2026-06-05T12:00:00Z',
      });
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).pull();
      expect(result.pulled, 2);
      expect((await dao.getProject('sp'))!.name, 'Hello');
      expect((await dao.getExpense('se'))!.projectId, 'sp');
      expect(await dao.getCursor(), '2026-06-05T12:00:00Z');
    });

    test('passes the shared cursor as ?since', () async {
      final dao = await _freshDao();
      await dao.setCursor('2026-06-05T09:00:00Z');
      final transport = _FakeTransport(changesResponse: {
        'projects': [],
        'expenses': [],
        'deleted_projects': [],
        'deleted_expenses': [],
        'now': '2026-06-05T12:00:00Z',
      });
      await BudgetsSync(dao, BudgetsRepository(transport)).pull();
      final call =
          transport.calls.firstWhere((c) => c.path.contains('/changes'));
      expect(call.query, containsPair('since', '2026-06-05T09:00:00Z'));
    });

    test('server wins when local project is NOT dirty', () async {
      final dao = await _freshDao();
      await dao.upsertProjectFromServer(
        Project.fromJson(_serverProjectJson(id: 'x', name: 'Old')),
        serverUpdatedAt: '2026-06-05T10:00:00Z',
      );
      final transport = _FakeTransport(changesResponse: {
        'projects': [
          _serverProjectJson(
              id: 'x', name: 'New', updatedAt: '2026-06-05T11:00:00Z')
        ],
        'expenses': [],
        'deleted_projects': [],
        'deleted_expenses': [],
        'now': '2026-06-05T12:00:00Z',
      });
      await BudgetsSync(dao, BudgetsRepository(transport)).pull();
      expect((await dao.getProject('x'))!.name, 'New');
      expect(await dao.readConflicts(), isEmpty);
    });

    test(
        'server wins on dirty local project with older local time → conflict logged',
        () async {
      final dao = await _freshDao(now: () => '2026-06-05T10:00:00Z');
      await dao.applyLocalProjectCreate('Local name', id: 'c1');

      final transport = _FakeTransport(changesResponse: {
        'projects': [
          _serverProjectJson(
              id: 'c1', name: 'Server name', updatedAt: '2026-06-05T11:00:00Z')
        ],
        'expenses': [],
        'deleted_projects': [],
        'deleted_expenses': [],
        'now': '2026-06-05T12:00:00Z',
      });
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).pull();

      expect(result.conflicts, 1);
      expect((await dao.getProject('c1'))!.name, 'Server name');
      final conflicts = await dao.readConflicts();
      final nameConflict = conflicts.firstWhere((c) => c.field == 'name');
      expect(nameConflict.local, 'Local name');
      expect(nameConflict.server, 'Server name');
    });

    test('local wins (kept) when dirty local project is strictly newer; no log',
        () async {
      final dao = await _freshDao(now: () => '2026-06-05T12:00:00Z');
      await dao.applyLocalProjectCreate('Local newest', id: 'c2');

      final transport = _FakeTransport(changesResponse: {
        'projects': [
          _serverProjectJson(
              id: 'c2', name: 'Server older', updatedAt: '2026-06-05T11:00:00Z')
        ],
        'expenses': [],
        'deleted_projects': [],
        'deleted_expenses': [],
        'now': '2026-06-05T13:00:00Z',
      });
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).pull();

      expect(result.pulled, 0);
      expect((await dao.getProject('c2'))!.name, 'Local newest');
      expect(await dao.readConflicts(), isEmpty);
      expect(await dao.dirtyProjectIds(), contains('c2'));
    });

    test('expense LWW: dirty local older → server wins + conflict logged',
        () async {
      final dao = await _freshDao(now: () => '2026-06-05T10:00:00Z');
      await dao.applyLocalExpenseCreate('p1', 5.0, 'Local desc', id: 'ec1');

      final transport = _FakeTransport(changesResponse: {
        'projects': [],
        'expenses': [
          _serverExpenseJson(
              id: 'ec1',
              amount: 9.0,
              description: 'Server desc',
              updatedAt: '2026-06-05T11:00:00Z')
        ],
        'deleted_projects': [],
        'deleted_expenses': [],
        'now': '2026-06-05T12:00:00Z',
      });
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).pull();

      expect(result.conflicts, greaterThanOrEqualTo(1));
      expect((await dao.getExpense('ec1'))!.description, 'Server desc');
      final conflicts = await dao.readConflicts();
      expect(conflicts.any((c) => c.field == 'description'), isTrue);
    });

    test(
        'expense is_favorite LWW: a genuine local↔server disagreement is logged '
        '(never silently dropped)', () async {
      final dao = await _freshDao(now: () => '2026-06-05T10:00:00Z');
      // Local dirty (older) expense that is NOT starred.
      await dao.applyLocalExpenseCreate('p1', 5.0, 'Same desc', id: 'favc1');

      final transport = _FakeTransport(changesResponse: {
        'projects': [],
        'expenses': [
          {
            ..._serverExpenseJson(
                id: 'favc1',
                amount: 5.0,
                description: 'Same desc',
                updatedAt: '2026-06-05T11:00:00Z'),
            'is_favorite': true, // server starred it → real disagreement
          }
        ],
        'deleted_projects': [],
        'deleted_expenses': [],
        'now': '2026-06-05T12:00:00Z',
      });
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).pull();

      // Server wins (newer) and the starred flag now reflects the server.
      expect((await dao.getExpense('favc1'))!.isFavorite, isTrue);
      expect(result.conflicts, greaterThanOrEqualTo(1));
      final favConflict = (await dao.readConflicts())
          .where((c) => c.field == 'is_favorite')
          .toList();
      expect(favConflict, hasLength(1));
      expect(favConflict.single.local, 'false');
      expect(favConflict.single.server, 'true');
    });

    test(
        'expense is_favorite LWW: matching stars do NOT false-conflict across '
        'the 0/1(cache) vs bool(server) shape divergence', () async {
      final dao = await _freshDao(now: () => '2026-06-05T10:00:00Z');
      // Local dirty (older) expense the user starred → cache stores INTEGER 1.
      await dao.applyLocalExpenseCreate('p1', 5.0, 'Same desc', id: 'favc2');
      await dao.applyLocalExpenseFavorite('favc2', true);

      final transport = _FakeTransport(changesResponse: {
        'projects': [],
        'expenses': [
          {
            // Server agrees on the star (JSON bool true) but changed the
            // description, so it wins LWW and the merge re-checks every field.
            ..._serverExpenseJson(
                id: 'favc2',
                amount: 5.0,
                description: 'Server desc',
                updatedAt: '2026-06-05T11:00:00Z'),
            'is_favorite': true,
          }
        ],
        'deleted_projects': [],
        'deleted_expenses': [],
        'now': '2026-06-05T12:00:00Z',
      });
      await BudgetsSync(dao, BudgetsRepository(transport)).pull();

      // The star matches (1 ↔ true) → no is_favorite conflict may be logged,
      // even though the description legitimately diverged.
      expect(
        (await dao.readConflicts()).where((c) => c.field == 'is_favorite'),
        isEmpty,
      );
      expect((await dao.getExpense('favc2'))!.isFavorite, isTrue);
    });

    test('applies project + expense tombstones (removes from lists)', () async {
      final dao = await _freshDao();
      await dao.upsertProjectFromServer(
        Project.fromJson(_serverProjectJson(id: 'gp', name: 'Doomed')),
        serverUpdatedAt: '2026-06-05T10:00:00Z',
      );
      await dao.upsertExpenseFromServer(
        Expense.fromJson(_serverExpenseJson(id: 'ge')),
        serverUpdatedAt: '2026-06-05T10:00:00Z',
      );
      final transport = _FakeTransport(changesResponse: {
        'projects': [],
        'expenses': [],
        'deleted_projects': ['gp'],
        'deleted_expenses': ['ge'],
        'now': '2026-06-05T12:00:00Z',
      });
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).pull();
      expect(result.deletedApplied, 2);
      expect((await dao.listProjects()).map((e) => e.id), isNot(contains('gp')));
      expect((await dao.listExpenses()).map((e) => e.id), isNot(contains('ge')));
    });

    test('pull network failure leaves the cursor untouched', () async {
      final dao = await _freshDao();
      await dao.setCursor('2026-06-05T09:00:00Z');
      final transport = _FailingGetTransport();
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).pull();
      expect(result.pullFailed, isTrue);
      expect(await dao.getCursor(), '2026-06-05T09:00:00Z');
    });

    // ── H1: server tombstone vs an unsynced local edit ──

    test(
        'H1: server delete of a DIRTY local project logs a conflict + reconciles '
        'the queued outbox op (delete-wins, never silent)', () async {
      final dao = await _freshDao(now: () => '2026-06-05T10:00:00Z');
      await dao.applyLocalProjectCreate('My unsynced project', id: 'h1');
      expect(await dao.outboxCount(), 1);

      final transport = _FakeTransport(changesResponse: {
        'projects': [],
        'expenses': [],
        'deleted_projects': ['h1'],
        'deleted_expenses': [],
        'now': '2026-06-05T12:00:00Z',
      });
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).pull();

      expect(result.deletedApplied, 1);
      expect(result.conflicts, greaterThanOrEqualTo(1));
      final conflicts = await dao.readConflicts();
      final nameConflict = conflicts.firstWhere((c) => c.field == 'name');
      expect(nameConflict.local, 'My unsynced project');
      expect(nameConflict.server, isNull); // server-deleted → no server value
      expect(await dao.outboxCount(), 0); // queued create reconciled away
      expect((await dao.listProjects()).map((e) => e.id), isNot(contains('h1')));
    });

    test('H1: server delete of a DIRTY local expense reconciles the queued op',
        () async {
      final dao = await _freshDao(now: () => '2026-06-05T10:00:00Z');
      await dao.applyLocalExpenseCreate('p1', 7.0, 'Unsynced exp', id: 'he1');
      expect(await dao.outboxCount(), 1);

      final transport = _FakeTransport(changesResponse: {
        'projects': [],
        'expenses': [],
        'deleted_projects': [],
        'deleted_expenses': ['he1'],
        'now': '2026-06-05T12:00:00Z',
      });
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).pull();

      expect(result.deletedApplied, 1);
      expect(result.conflicts, greaterThanOrEqualTo(1));
      expect(await dao.outboxCount(), 0);
      expect((await dao.listExpenses()).map((e) => e.id), isNot(contains('he1')));
    });

    test(
        'H1 cascade: server delete of a project sweeps its CLEAN local child '
        'expenses (no orphans) — children become locally deleted', () async {
      final dao = await _freshDao(now: () => '2026-06-05T10:00:00Z');
      // A clean (already-synced) project with two clean child expenses.
      await dao.upsertProjectFromServer(
        Project.fromJson(_serverProjectJson(id: 'par', name: 'Parent')),
        serverUpdatedAt: '2026-06-05T10:00:00Z',
      );
      await dao.upsertExpenseFromServer(
        Expense.fromJson(_serverExpenseJson(id: 'ch1', projectId: 'par')),
        serverUpdatedAt: '2026-06-05T10:00:00Z',
      );
      await dao.upsertExpenseFromServer(
        Expense.fromJson(_serverExpenseJson(id: 'ch2', projectId: 'par')),
        serverUpdatedAt: '2026-06-05T10:00:00Z',
      );
      expect((await dao.listExpenses()).map((e) => e.id),
          containsAll(['ch1', 'ch2']));

      // The server tombstones ONLY the project (does NOT enumerate the children
      // in deleted_expenses) — the exact shape that used to orphan children.
      final transport = _FakeTransport(changesResponse: {
        'projects': [],
        'expenses': [],
        'deleted_projects': ['par'],
        'deleted_expenses': [],
        'now': '2026-06-05T12:00:00Z',
      });
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).pull();

      expect(result.deletedApplied, 1);
      // The parent + BOTH children are gone from the local lists (no orphans).
      expect((await dao.listProjects()).map((e) => e.id), isNot(contains('par')));
      final remainingExpenses = (await dao.listExpenses()).map((e) => e.id);
      expect(remainingExpenses, isNot(contains('ch1')));
      expect(remainingExpenses, isNot(contains('ch2')));
      // Clean children cascaded silently — no conflict rows logged for them.
      expect(await dao.readConflicts(), isEmpty);
    });

    test(
        'H1 cascade: a DIRTY child expense logs a conflict + reconciles its '
        'queued outbox op before the project delete clobbers it', () async {
      final dao = await _freshDao(now: () => '2026-06-05T10:00:00Z');
      await dao.upsertProjectFromServer(
        Project.fromJson(_serverProjectJson(id: 'par2', name: 'Parent2')),
        serverUpdatedAt: '2026-06-05T10:00:00Z',
      );
      // An unsynced (dirty) local child expense with a queued create op.
      await dao.applyLocalExpenseCreate('par2', 42.0, 'Unsynced child',
          id: 'dch');
      expect(await dao.outboxCount(), 1);

      final transport = _FakeTransport(changesResponse: {
        'projects': [],
        'expenses': [],
        'deleted_projects': ['par2'],
        'deleted_expenses': [],
        'now': '2026-06-05T12:00:00Z',
      });
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).pull();

      expect(result.deletedApplied, 1);
      // The dirty child's clobbered edit is logged (never silent).
      expect(result.conflicts, greaterThanOrEqualTo(1));
      final conflicts = await dao.readConflicts();
      expect(conflicts.any((c) => c.field == 'description'), isTrue);
      final descConflict =
          conflicts.firstWhere((c) => c.field == 'description');
      expect(descConflict.local, 'Unsynced child');
      expect(descConflict.server, isNull); // server-deleted → no server value
      // The now-moot queued create was reconciled away, and the child is gone.
      expect(await dao.outboxCount(), 0);
      expect((await dao.listExpenses()).map((e) => e.id), isNot(contains('dch')));
    });

    test('H1: server delete of a CLEAN local row applies silently (no conflict)',
        () async {
      final dao = await _freshDao();
      await dao.upsertProjectFromServer(
        Project.fromJson(_serverProjectJson(id: 'clean', name: 'Synced')),
        serverUpdatedAt: '2026-06-05T10:00:00Z',
      );
      final transport = _FakeTransport(changesResponse: {
        'projects': [],
        'expenses': [],
        'deleted_projects': ['clean'],
        'deleted_expenses': [],
        'now': '2026-06-05T12:00:00Z',
      });
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).pull();
      expect(result.deletedApplied, 1);
      expect(result.conflicts, 0);
      expect(await dao.readConflicts(), isEmpty);
    });

    // ── M1: conflict-table dedup ──

    test('M1: re-observing the SAME conflict does not duplicate the row',
        () async {
      final dao = await _freshDao(now: () => '2026-06-05T10:00:00Z');
      await dao.applyLocalProjectCreate('Local name', id: 'm1');
      final changes = {
        'projects': [
          _serverProjectJson(
              id: 'm1', name: 'Server name', updatedAt: '2026-06-05T11:00:00Z')
        ],
        'expenses': [],
        'deleted_projects': [],
        'deleted_expenses': [],
        'now': '2026-06-05T12:00:00Z',
      };
      await BudgetsSync(
              dao, BudgetsRepository(_FakeTransport(changesResponse: changes)))
          .pull();
      final afterFirst = await dao.readConflicts();
      // Make the row dirty again with the SAME local value vs the same server.
      await dao.applyLocalProjectUpdate('m1', name: 'Local name');
      await BudgetsSync(
              dao, BudgetsRepository(_FakeTransport(changesResponse: changes)))
          .pull();
      final afterSecond = await dao.readConflicts();
      final nameRows = afterSecond.where((c) => c.field == 'name').toList();
      expect(nameRows,
          hasLength(afterFirst.where((c) => c.field == 'name').length));
    });

    // ── M3: empty server `now` ──

    test('M3: empty `now` with no datable rows → pull FAILS, cursor untouched',
        () async {
      final dao = await _freshDao();
      await dao.setCursor('2026-06-05T09:00:00Z');
      final transport = _FakeTransport(changesResponse: {
        'projects': [],
        'expenses': [],
        'deleted_projects': [],
        'deleted_expenses': [],
        'now': '',
      });
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).pull();
      expect(result.pullFailed, isTrue);
      expect(await dao.getCursor(), '2026-06-05T09:00:00Z');
    });

    test('M3: empty `now` falls back to the max row updated_at across BOTH',
        () async {
      final dao = await _freshDao();
      final transport = _FakeTransport(changesResponse: {
        'projects': [
          _serverProjectJson(id: 'a', updatedAt: '2026-06-05T11:00:00Z'),
        ],
        'expenses': [
          _serverExpenseJson(
              id: 'b', projectId: 'a', updatedAt: '2026-06-05T13:00:00Z'),
        ],
        'deleted_projects': [],
        'deleted_expenses': [],
        'now': '',
      });
      final result =
          await BudgetsSync(dao, BudgetsRepository(transport)).pull();
      expect(result.pullFailed, isFalse);
      expect(result.pulled, 2);
      // Newest updated_at across projects + expenses wins.
      expect(await dao.getCursor(), '2026-06-05T13:00:00Z');
    });
  });

  // ── SYNC orchestration ────────────────────────────────────────────────────

  group('BudgetsSync.sync', () {
    test('push then pull; guards against concurrent runs', () async {
      final dao = await _freshDao();
      await dao.applyLocalProjectCreate('A', id: 'a1');
      final transport = _FakeTransport(changesResponse: {
        'projects': [_serverProjectJson(id: 'srv', name: 'Server one')],
        'expenses': [],
        'deleted_projects': [],
        'deleted_expenses': [],
        'now': '2026-06-05T12:00:00Z',
      });
      final sync = BudgetsSync(dao, BudgetsRepository(transport));

      final result = await sync.sync();
      expect(result.pushed, 1);
      expect(result.pulled, 1);
      expect(sync.isRunning, isFalse);

      expect(await dao.getProject('srv'), isNotNull);
      expect(await dao.outboxCount(), 0);
    });

    test('a concurrent sync() call is a no-op while one is running', () async {
      final dao = await _freshDao();
      final sync = BudgetsSync(dao, BudgetsRepository(_FakeTransport()));
      final a = sync.sync();
      final b = sync.sync();
      final results = await Future.wait([a, b]);
      expect(results, hasLength(2));
    });
  });
}

// ── test helpers ─────────────────────────────────────────────────────────────

/// A transport whose GET always throws a network error (for pull failure test).
class _FailingGetTransport implements BudgetsTransport {
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
