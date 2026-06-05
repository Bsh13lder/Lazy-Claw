import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/budgets_dao.dart';
import 'package:lazyclaw_mobile/models/expense.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

int _dbCounter = 0;

/// Spin up a real in-memory SQLite (via ffi) with the production schema, so the
/// DAO logic is verified against the actual engine — no hand-rolled fake DB.
/// Each call gets an ISOLATED in-memory DB so state never bleeds between tests.
Future<BudgetsDao> _freshDao({String Function()? now}) async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:budgetmem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return BudgetsDao(db, now: now);
}

Project _serverProject({
  String id = 'p1',
  String name = 'Server project',
  double budget = 1000.0,
}) =>
    Project(
      id: id,
      name: name,
      budget: budget,
      currency: 'USD',
      status: 'active',
      spent: 0.0,
      remaining: budget,
    );

Expense _serverExpense({
  String id = 'e1',
  String projectId = 'p1',
  double amount = 50.0,
  String description = 'Server expense',
}) =>
    Expense(
      id: id,
      projectId: projectId,
      amount: amount,
      currency: 'USD',
      description: description,
      status: 'posted',
    );

void main() {
  setUpAll(() => sqfliteFfiInit());

  // ── Projects ───────────────────────────────────────────────────────────────

  group('BudgetsDao local project create', () {
    test('mints a UUID, stores dirty row, enqueues a project create', () async {
      final dao = await _freshDao();
      final project = await dao.applyLocalProjectCreate('Marketing');

      expect(project.id, isNotEmpty);
      expect(project.name, 'Marketing');
      expect(project.status, 'active');

      final stored = await dao.getProject(project.id);
      expect(stored, isNotNull);

      expect(await dao.dirtyProjectIds(), contains(project.id));

      final outbox = await dao.readBudgetsOutbox();
      expect(outbox, hasLength(1));
      expect(outbox.first.op, BudgetsOutboxOp.create);
      expect(outbox.first.entity, kProjectEntity);
      expect(outbox.first.entityId, project.id);
      expect(outbox.first.payload['id'], project.id);
      expect(outbox.first.payload['name'], 'Marketing');
    });

    test('honours a caller-supplied id (idempotent replay)', () async {
      final dao = await _freshDao();
      final p =
          await dao.applyLocalProjectCreate('Pinned', id: 'fixed-proj');
      expect(p.id, 'fixed-proj');
      final outbox = await dao.readBudgetsOutbox();
      expect(outbox.first.payload['id'], 'fixed-proj');
    });

    test('passes budget into the outbox payload', () async {
      final dao = await _freshDao();
      await dao.applyLocalProjectCreate('Funded', budget: 500.0);
      final outbox = await dao.readBudgetsOutbox();
      expect(outbox.first.payload['budget'], 500.0);
    });

    test('omits budget from the payload when null', () async {
      final dao = await _freshDao();
      await dao.applyLocalProjectCreate('Unfunded');
      final outbox = await dao.readBudgetsOutbox();
      expect(outbox.first.payload.containsKey('budget'), isFalse);
    });

    test('listProjects hides tombstoned projects', () async {
      final dao = await _freshDao();
      final a = await dao.applyLocalProjectCreate('A');
      await dao.applyLocalProjectCreate('B');
      await dao.applyLocalProjectDelete(a.id);

      final projects = await dao.listProjects();
      expect(projects.map((p) => p.name), ['B']);
    });
  });

  group('BudgetsDao project update / delete', () {
    test('update bumps dirty + enqueues an update with the patch', () async {
      final dao = await _freshDao();
      final p = await dao.applyLocalProjectCreate('Old name');
      final updated = await dao.applyLocalProjectUpdate(p.id, name: 'New name');
      expect(updated!.name, 'New name');

      final stored = await dao.getProject(p.id);
      expect(stored!.name, 'New name');

      final outbox = await dao.readBudgetsOutbox();
      final updateItem = outbox.firstWhere((o) => o.op == BudgetsOutboxOp.update);
      expect(updateItem.entity, kProjectEntity);
      expect(updateItem.payload['name'], 'New name');
      expect(updateItem.payload['id'], p.id);
    });

    test('delete tombstones + enqueues a delete', () async {
      final dao = await _freshDao();
      final p = await dao.applyLocalProjectCreate('Remove me');
      final ok = await dao.applyLocalProjectDelete(p.id);
      expect(ok, isTrue);

      expect(await dao.getProject(p.id), isNotNull); // tombstone present
      expect((await dao.listProjects()).map((e) => e.id), isNot(contains(p.id)));

      final outbox = await dao.readBudgetsOutbox();
      expect(outbox.any((o) => o.op == BudgetsOutboxOp.delete), isTrue);
    });

    test('update/delete on a missing project is a no-op', () async {
      final dao = await _freshDao();
      expect(await dao.applyLocalProjectUpdate('nope', name: 'x'), isNull);
      expect(await dao.applyLocalProjectDelete('nope'), isFalse);
      expect(await dao.readBudgetsOutbox(), isEmpty);
    });
  });

  // ── Expenses ─────────────────────────────────────────────────────────────

  group('BudgetsDao local expense create', () {
    test('mints a UUID, stores dirty row, enqueues an expense create',
        () async {
      final dao = await _freshDao();
      final exp = await dao.applyLocalExpenseCreate('p1', 25.0, 'Lunch');

      expect(exp.id, isNotEmpty);
      expect(exp.amount, 25.0);
      expect(exp.projectId, 'p1');

      final stored = await dao.getExpense(exp.id);
      expect(stored, isNotNull);

      expect(await dao.dirtyExpenseIds(), contains(exp.id));

      final outbox = await dao.readBudgetsOutbox();
      expect(outbox, hasLength(1));
      expect(outbox.first.op, BudgetsOutboxOp.create);
      expect(outbox.first.entity, kExpenseEntity);
      expect(outbox.first.payload['id'], exp.id);
      expect(outbox.first.payload['project_id'], 'p1');
      expect(outbox.first.payload['amount'], 25.0);
      expect(outbox.first.payload['description'], 'Lunch');
    });

    test('honours a caller-supplied id (idempotent replay)', () async {
      final dao = await _freshDao();
      final e = await dao.applyLocalExpenseCreate('p1', 10.0, 'X',
          id: 'fixed-exp');
      expect(e.id, 'fixed-exp');
      expect((await dao.readBudgetsOutbox()).first.payload['id'], 'fixed-exp');
    });

    test('passes vendor into the payload when provided', () async {
      final dao = await _freshDao();
      await dao.applyLocalExpenseCreate('p1', 99.0, 'Hosting', vendor: 'AWS');
      final outbox = await dao.readBudgetsOutbox();
      expect(outbox.first.payload['vendor'], 'AWS');
    });

    test('listExpenses hides tombstoned + void expenses', () async {
      final dao = await _freshDao();
      final a = await dao.applyLocalExpenseCreate('p1', 5.0, 'A');
      await dao.applyLocalExpenseCreate('p1', 6.0, 'B');
      await dao.applyLocalExpenseDelete(a.id);

      final expenses = await dao.listExpenses();
      expect(expenses.map((e) => e.description), ['B']);
    });
  });

  group('BudgetsDao expense delete', () {
    test('delete tombstones + enqueues a delete', () async {
      final dao = await _freshDao();
      final e = await dao.applyLocalExpenseCreate('p1', 12.0, 'Remove me');
      final ok = await dao.applyLocalExpenseDelete(e.id);
      expect(ok, isTrue);

      expect(await dao.getExpense(e.id), isNotNull); // tombstone present
      expect((await dao.listExpenses()).map((x) => x.id), isNot(contains(e.id)));

      final outbox = await dao.readBudgetsOutbox();
      expect(outbox.any((o) => o.op == BudgetsOutboxOp.delete), isTrue);
    });

    test('delete on a missing expense is a no-op', () async {
      final dao = await _freshDao();
      expect(await dao.applyLocalExpenseDelete('nope'), isFalse);
      expect(await dao.readBudgetsOutbox(), isEmpty);
    });
  });

  // ── Shared outbox / cursor / conflicts ─────────────────────────────────────

  group('BudgetsDao shared outbox (both entities interleaved)', () {
    test('readBudgetsOutbox returns project + expense ops in seq order',
        () async {
      final dao = await _freshDao();
      final p = await dao.applyLocalProjectCreate('Proj', id: 'p1');
      await dao.applyLocalExpenseCreate('p1', 10.0, 'Exp', id: 'e1');
      await dao.applyLocalProjectUpdate(p.id, name: 'Proj2');

      final outbox = await dao.readBudgetsOutbox();
      expect(outbox.map((o) => o.entity),
          [kProjectEntity, kExpenseEntity, kProjectEntity]);
      // seq strictly increasing.
      for (var i = 1; i < outbox.length; i++) {
        expect(outbox[i].seq, greaterThan(outbox[i - 1].seq));
      }
    });

    test('outboxCount counts only budgets-domain rows', () async {
      final dao = await _freshDao();
      await dao.applyLocalProjectCreate('P', id: 'p1');
      await dao.applyLocalExpenseCreate('p1', 1.0, 'E', id: 'e1');
      expect(await dao.outboxCount(), 2);
    });
  });

  group('BudgetsDao commitPush + retry bookkeeping', () {
    test('commitPush atomically dequeues + clears dirty (project); idempotent',
        () async {
      final dao = await _freshDao();
      final p = await dao.applyLocalProjectCreate('A', id: 'cp1');
      final seq = (await dao.readBudgetsOutbox()).first.seq;

      await dao.commitPush(seq, kProjectEntity, p.id);
      expect(await dao.readBudgetsOutbox(), isEmpty);
      expect(await dao.dirtyProjectIds(), isEmpty);
      expect(await dao.getProject(p.id), isNotNull);

      // Replay (crash-retry) must be a safe no-op.
      await dao.commitPush(seq, kProjectEntity, p.id);
      expect(await dao.dirtyProjectIds(), isEmpty);
    });

    test('commitPush hard-removes a pushed project tombstone', () async {
      final dao = await _freshDao();
      final p = await dao.applyLocalProjectCreate('A', id: 'cp2');
      await dao.applyLocalProjectDelete(p.id);
      final delSeq = (await dao.readBudgetsOutbox())
          .firstWhere((o) => o.op == BudgetsOutboxOp.delete)
          .seq;
      await dao.commitPush(delSeq, kProjectEntity, p.id);
      expect(await dao.getProject(p.id), isNull);
    });

    test('commitPush hard-removes a pushed expense tombstone', () async {
      final dao = await _freshDao();
      final e = await dao.applyLocalExpenseCreate('p1', 5.0, 'A', id: 'cpe');
      await dao.applyLocalExpenseDelete(e.id);
      final delSeq = (await dao.readBudgetsOutbox())
          .firstWhere((o) => o.op == BudgetsOutboxOp.delete)
          .seq;
      await dao.commitPush(delSeq, kExpenseEntity, e.id);
      expect(await dao.getExpense(e.id), isNull);
    });

    test('bumpOutboxAttempts increments + returns the new count', () async {
      final dao = await _freshDao();
      await dao.applyLocalProjectCreate('A', id: 'ba1');
      final seq = (await dao.readBudgetsOutbox()).first.seq;
      expect(await dao.bumpOutboxAttempts(seq), 1);
      expect(await dao.bumpOutboxAttempts(seq), 2);
      expect((await dao.readBudgetsOutbox()).first.attempts, 2);
    });

    test('deadLetterOutboxItem drops the row but leaves the cache dirty',
        () async {
      final dao = await _freshDao();
      final p = await dao.applyLocalProjectCreate('A', id: 'dl1');
      final seq = (await dao.readBudgetsOutbox()).first.seq;
      await dao.deadLetterOutboxItem(seq);
      expect(await dao.readBudgetsOutbox(), isEmpty);
      expect(await dao.dirtyProjectIds(), contains(p.id));
    });

    test('deleteOutboxForEntity removes every queued op for one entity+id',
        () async {
      final dao = await _freshDao();
      final p = await dao.applyLocalProjectCreate('A', id: 'de1');
      await dao.applyLocalProjectUpdate(p.id, name: 'A2');
      await dao.applyLocalProjectDelete(p.id);
      await dao.applyLocalProjectCreate('Other', id: 'other');
      final removed = await dao.deleteOutboxForEntity(kProjectEntity, p.id);
      expect(removed, 3);
      final remaining = await dao.readBudgetsOutbox();
      expect(remaining.every((o) => o.entityId == 'other'), isTrue);
    });

    test(
        'deleteOutboxForEntity is entity-scoped: a project tombstone never wipes '
        'a sibling EXPENSE outbox row that shares the same id (C1)', () async {
      final dao = await _freshDao();
      // A project and an expense deliberately minted with the SAME id.
      await dao.applyLocalProjectCreate('Collide', id: 'same');
      await dao.applyLocalExpenseCreate('proj', 5.0, 'Collide exp', id: 'same');
      expect(await dao.outboxCount(), 2);

      // Scoped to the project entity → only the project row is removed.
      final removed = await dao.deleteOutboxForEntity(kProjectEntity, 'same');
      expect(removed, 1);

      final remaining = await dao.readBudgetsOutbox();
      expect(remaining, hasLength(1));
      expect(remaining.single.entity, kExpenseEntity);
      expect(remaining.single.entityId, 'same');
    });
  });

  group('BudgetsDao upsertFromServer + tombstone', () {
    test('writes a clean server project row', () async {
      final dao = await _freshDao();
      await dao.upsertProjectFromServer(
        _serverProject(id: 'srv', name: 'From server'),
        serverUpdatedAt: '2026-06-05T11:00:00Z',
      );
      final stored = await dao.getProject('srv');
      expect(stored!.name, 'From server');
      expect(await dao.dirtyProjectIds(), isEmpty);
      final row = await dao.getProjectRow('srv');
      expect(row!['updated_at'], '2026-06-05T11:00:00Z');
    });

    test('writes a clean server expense row', () async {
      final dao = await _freshDao();
      await dao.upsertExpenseFromServer(
        _serverExpense(id: 'srv', description: 'From server'),
        serverUpdatedAt: '2026-06-05T11:00:00Z',
      );
      final stored = await dao.getExpense('srv');
      expect(stored!.description, 'From server');
      expect(await dao.dirtyExpenseIds(), isEmpty);
      final row = await dao.getExpenseRow('srv');
      expect(row!['updated_at'], '2026-06-05T11:00:00Z');
    });

    test('applyServerProjectDelete tombstones an existing row', () async {
      final dao = await _freshDao();
      await dao.upsertProjectFromServer(_serverProject(id: 'srv'));
      await dao.applyServerProjectDelete('srv');
      expect((await dao.listProjects()).map((e) => e.id), isNot(contains('srv')));
    });

    test('applyServerExpenseDelete tombstones an existing row', () async {
      final dao = await _freshDao();
      await dao.upsertExpenseFromServer(_serverExpense(id: 'srv'));
      await dao.applyServerExpenseDelete('srv');
      expect((await dao.listExpenses()).map((e) => e.id), isNot(contains('srv')));
    });
  });

  group('BudgetsDao cursor + conflicts', () {
    test('the shared budgets cursor round-trips', () async {
      final dao = await _freshDao();
      expect(await dao.getCursor(), isNull);
      await dao.setCursor('2026-06-05T12:00:00Z');
      expect(await dao.getCursor(), '2026-06-05T12:00:00Z');
      await dao.setCursor('2026-06-05T13:00:00Z');
      expect(await dao.getCursor(), '2026-06-05T13:00:00Z');
    });

    test('logs and reads conflicts (never silently dropped)', () async {
      final dao = await _freshDao();
      await dao.logConflict(
        id: 'p1',
        field: 'name',
        local: 'Local name',
        server: 'Server name',
        at: '2026-06-05T12:00:00Z',
      );
      final conflicts = await dao.readConflicts();
      expect(conflicts, hasLength(1));
      expect(conflicts.first.field, 'name');
      expect(conflicts.first.local, 'Local name');
      expect(conflicts.first.server, 'Server name');
    });

    test('dedups an identical conflict (incl. null server)', () async {
      final dao = await _freshDao();
      await dao.logConflict(id: 'p1', field: 'name', local: 'A', server: null);
      await dao.logConflict(id: 'p1', field: 'name', local: 'A', server: null);
      await dao.logConflict(id: 'p1', field: 'name', local: 'A', server: 'B');
      final conflicts = await dao.readConflicts();
      expect(conflicts, hasLength(2));
    });
  });
}
