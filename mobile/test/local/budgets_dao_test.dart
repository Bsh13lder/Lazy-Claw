import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/budgets_dao.dart';
import 'package:lazyclaw_mobile/models/budget_entry.dart';
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
  String? taskId,
  String? subtaskId,
}) =>
    Expense(
      id: id,
      projectId: projectId,
      amount: amount,
      currency: 'USD',
      description: description,
      status: 'posted',
      taskId: taskId,
      subtaskId: subtaskId,
    );

BudgetEntry _serverBudgetEntry({
  String id = 'be1',
  String projectId = 'p1',
  double amount = 200.0,
  String? source = 'Client payment',
  String kind = 'credit',
  String? createdAt = '2026-07-01T10:00:00Z',
}) =>
    BudgetEntry(
      id: id,
      projectId: projectId,
      amount: amount,
      currency: 'USD',
      source: source,
      kind: kind,
      createdAt: createdAt,
    );

void main() {
  setUpAll(() => sqfliteFfiInit());

  // ── Self-heal stranded creates (reserva-1000 data-loss recovery) ────────────

  group('reenqueueOrphanedCreates', () {
    test('re-enqueues a stranded expense create (dropped op, never synced)',
        () async {
      final dao = await _freshDao();
      // An offline-created expense: dirty=1, last_synced_at=null, create queued.
      await dao.applyLocalExpenseCreate('p1', 1000.0, 'reserva',
          id: 'exp-reserva', currency: 'EUR');
      // Simulate the create op being dropped (dead-lettered / old silent drain).
      final before = await dao.readBudgetsOutbox();
      for (final o in before) {
        await dao.deleteOutboxItem(o.seq);
      }
      expect(await dao.readBudgetsOutbox(), isEmpty);

      final healed = await dao.reenqueueOrphanedCreates();

      expect(healed, 1);
      final outbox = await dao.readBudgetsOutbox();
      expect(outbox.length, 1);
      final op = outbox.single;
      expect(op.op, BudgetsOutboxOp.create);
      expect(op.isExpense, isTrue);
      expect(op.entityId, 'exp-reserva');
      expect(op.payload['project_id'], 'p1');
      expect((op.payload['amount'] as num).toDouble(), 1000.0);
      expect(op.payload['description'], 'reserva');
      expect(op.payload['currency'], 'EUR');
    });

    test('does NOT duplicate when a pending op already exists', () async {
      final dao = await _freshDao();
      await dao.applyLocalExpenseCreate('p1', 12.0, 'coffee', id: 'e1');
      // Op is still queued → not an orphan.
      final healed = await dao.reenqueueOrphanedCreates();
      expect(healed, 0);
      expect((await dao.readBudgetsOutbox()).length, 1);
    });

    test('skips a row already flagged create_rejected (no infinite loop)',
        () async {
      final dao = await _freshDao();
      await dao.applyLocalExpenseCreate('gone', 5.0, 'bad', id: 'e-bad');
      for (final o in await dao.readBudgetsOutbox()) {
        await dao.deleteOutboxItem(o.seq);
      }
      await dao.logConflict(
          id: 'e-bad', field: 'create_rejected', local: 'HTTP 400', server: null);

      final healed = await dao.reenqueueOrphanedCreates();

      expect(healed, 0);
      expect(await dao.readBudgetsOutbox(), isEmpty);
    });

    test('skips a synced row whose UPDATE op was drained (last_synced_at set)',
        () async {
      final dao = await _freshDao();
      // Pulled from server → last_synced_at set, dirty=0.
      await dao.upsertExpenseFromServer(_serverExpense(id: 'e-synced'));
      // A local edit → dirty=1 but last_synced_at stays (it DID reach server once).
      await dao.applyLocalExpenseUpdate('e-synced', amount: 99.0);
      for (final o in await dao.readBudgetsOutbox()) {
        await dao.deleteOutboxItem(o.seq);
      }

      final healed = await dao.reenqueueOrphanedCreates();

      // Not a stranded CREATE — it exists server-side; the next pull reconciles it.
      expect(healed, 0);
    });

    test('re-enqueues a stranded PROJECT before its stranded expense', () async {
      final dao = await _freshDao();
      await dao.applyLocalProjectCreate('Orphan proj', id: 'proj-x');
      await dao.applyLocalExpenseCreate('proj-x', 7.0, 'thing', id: 'exp-x');
      for (final o in await dao.readBudgetsOutbox()) {
        await dao.deleteOutboxItem(o.seq);
      }

      final healed = await dao.reenqueueOrphanedCreates();

      expect(healed, 2);
      final outbox = await dao.readBudgetsOutbox();
      // Parent project must drain first (lower seq) so the child's POST finds it.
      expect(outbox.first.isProject, isTrue);
      expect(outbox.first.entityId, 'proj-x');
      expect(outbox[1].isExpense, isTrue);
    });
  });

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

  // ── Expense update: taskId (null-vs-absent is load-bearing) ────────────────

  group('BudgetsDao expense update taskId', () {
    test('taskIdSet:true writes task_id into the cache row + outbox payload',
        () async {
      final dao = await _freshDao();
      await dao.upsertExpenseFromServer(_serverExpense(id: 'e-task'));

      final updated = await dao.applyLocalExpenseUpdate('e-task',
          taskId: 't1', taskIdSet: true);
      expect(updated!.taskId, 't1');

      final row = await dao.getExpenseRow('e-task');
      expect(row?['task_id'], 't1');

      final outbox = await dao.readBudgetsOutbox();
      final updateItem =
          outbox.firstWhere((o) => o.op == BudgetsOutboxOp.update);
      expect(updateItem.payload['task_id'], 't1');
    });

    test(
        'explicit clear: taskId:null + taskIdSet:true keeps the task_id key '
        'present (JSON null) in both the cache write and the outbox payload',
        () async {
      final dao = await _freshDao();
      await dao.upsertExpenseFromServer(
          _serverExpense(id: 'e-clear', taskId: 't0'));

      final updated = await dao.applyLocalExpenseUpdate('e-clear',
          taskId: null, taskIdSet: true);
      expect(updated!.taskId, isNull);

      final row = await dao.getExpenseRow('e-clear');
      expect(row?['task_id'], isNull);

      final outbox = await dao.readBudgetsOutbox();
      final updateItem =
          outbox.firstWhere((o) => o.op == BudgetsOutboxOp.update);
      // The key must be PRESENT (mapping to JSON null on replay), not merely
      // absent-and-therefore-null — that's what tells the server's
      // exclude_unset PATCH handler to actually clear the link.
      expect(updateItem.payload.containsKey('task_id'), isTrue);
      expect(updateItem.payload['task_id'], isNull);
    });

    test(
        'no taskIdSet: task_id key is absent from the outbox payload and the '
        'cached task_id is left untouched',
        () async {
      final dao = await _freshDao();
      await dao.upsertExpenseFromServer(
          _serverExpense(id: 'e-untouched', taskId: 't0'));

      final updated =
          await dao.applyLocalExpenseUpdate('e-untouched', description: 'x');
      expect(updated!.taskId, 't0');

      final row = await dao.getExpenseRow('e-untouched');
      expect(row?['task_id'], 't0');

      final outbox = await dao.readBudgetsOutbox();
      final updateItem =
          outbox.firstWhere((o) => o.op == BudgetsOutboxOp.update);
      expect(updateItem.payload.containsKey('task_id'), isFalse);
    });
  });

  // ── Expense update: subtaskId (exact mirror of the taskId group above) ─────

  group('BudgetsDao expense update subtaskId', () {
    test(
        'upsertExpenseFromServer round-trips subtask_id into the cache row '
        'and the read-back Expense', () async {
      final dao = await _freshDao();
      await dao.upsertExpenseFromServer(
          _serverExpense(id: 'e-srv-sub', taskId: 't1', subtaskId: 's1'));

      final expense = await dao.getExpense('e-srv-sub');
      expect(expense!.taskId, 't1');
      expect(expense.subtaskId, 's1');

      final row = await dao.getExpenseRow('e-srv-sub');
      expect(row?['subtask_id'], 's1');
    });

    test(
        'subtaskIdSet:true writes subtask_id into the cache row + outbox '
        'payload', () async {
      final dao = await _freshDao();
      await dao.upsertExpenseFromServer(
          _serverExpense(id: 'e-subtask', taskId: 't1'));

      final updated = await dao.applyLocalExpenseUpdate('e-subtask',
          subtaskId: 's1', subtaskIdSet: true);
      expect(updated!.subtaskId, 's1');

      final row = await dao.getExpenseRow('e-subtask');
      expect(row?['subtask_id'], 's1');

      final outbox = await dao.readBudgetsOutbox();
      final updateItem =
          outbox.firstWhere((o) => o.op == BudgetsOutboxOp.update);
      expect(updateItem.payload['subtask_id'], 's1');
    });

    test(
        'explicit clear: subtaskId:null + subtaskIdSet:true keeps the '
        'subtask_id key present (JSON null) in both the cache write and the '
        'outbox payload', () async {
      final dao = await _freshDao();
      await dao.upsertExpenseFromServer(
          _serverExpense(id: 'e-sub-clear', taskId: 't1', subtaskId: 's0'));

      final updated = await dao.applyLocalExpenseUpdate('e-sub-clear',
          subtaskId: null, subtaskIdSet: true);
      expect(updated!.subtaskId, isNull);

      final row = await dao.getExpenseRow('e-sub-clear');
      expect(row?['subtask_id'], isNull);

      final outbox = await dao.readBudgetsOutbox();
      final updateItem =
          outbox.firstWhere((o) => o.op == BudgetsOutboxOp.update);
      expect(updateItem.payload.containsKey('subtask_id'), isTrue);
      expect(updateItem.payload['subtask_id'], isNull);
    });

    test(
        'no subtaskIdSet: subtask_id key is absent from the outbox payload '
        'and the cached subtask_id is left untouched', () async {
      final dao = await _freshDao();
      await dao.upsertExpenseFromServer(
          _serverExpense(id: 'e-sub-untouched', taskId: 't1', subtaskId: 's0'));

      final updated = await dao.applyLocalExpenseUpdate('e-sub-untouched',
          description: 'x');
      expect(updated!.subtaskId, 's0');

      final row = await dao.getExpenseRow('e-sub-untouched');
      expect(row?['subtask_id'], 's0');

      final outbox = await dao.readBudgetsOutbox();
      final updateItem =
          outbox.firstWhere((o) => o.op == BudgetsOutboxOp.update);
      expect(updateItem.payload.containsKey('subtask_id'), isFalse);
    });

    test(
        'a plain applyLocalExpenseCreate never carries subtask_id (mirrors '
        'task_id — the link is only ever set via a later PATCH)', () async {
      final dao = await _freshDao();
      final created =
          await dao.applyLocalExpenseCreate('p1', 5.0, 'New expense');
      expect(created.subtaskId, isNull);
      expect(created.taskId, isNull);

      final outbox = await dao.readBudgetsOutbox();
      final createItem =
          outbox.firstWhere((o) => o.op == BudgetsOutboxOp.create);
      expect(createItem.payload.containsKey('subtask_id'), isFalse);
      expect(createItem.payload.containsKey('task_id'), isFalse);
    });
  });

  group('BudgetsDao expense delete (create/delete collapse)', () {
    test(
        'deleting a NEVER-SYNCED expense cancels its pending create '
        '(no delete op, row hard-removed)', () async {
      final dao = await _freshDao();
      await dao.applyLocalProjectCreate('P', id: 'p1');
      await dao.applyLocalExpenseCreate('p1', 1000.0, 'reserva', id: 'e1');
      // The un-synced create is queued.
      expect(
        (await dao.readBudgetsOutbox())
            .where((o) => o.isExpense && o.entityId == 'e1')
            .length,
        1,
      );

      final ok = await dao.applyLocalExpenseDelete('e1');
      expect(ok, isTrue);

      // The queued create is annihilated — NO create AND NO delete op remains
      // for e1 (avoids a pointless create→delete server round-trip and the
      // reappearance window if sync interrupts between the two pushes).
      expect(
        (await dao.readBudgetsOutbox()).where((o) => o.entityId == 'e1'),
        isEmpty,
      );
      // The row is hard-removed locally, not left as a dirty tombstone.
      expect(await dao.getExpenseRow('e1'), isNull);
    });

    test('deleting a SYNCED expense tombstones + enqueues a delete', () async {
      final dao = await _freshDao();
      // Pulled from the server → last_synced_at set, dirty=0, no pending create.
      await dao.upsertExpenseFromServer(_serverExpense(id: 'e-synced'));

      final ok = await dao.applyLocalExpenseDelete('e-synced');
      expect(ok, isTrue);

      // A server-backed row must be tombstoned + a delete queued so the server
      // learns about the removal.
      final row = await dao.getExpenseRow('e-synced');
      expect(row?['deleted'], 1);
      expect(
        (await dao.readBudgetsOutbox()).any((o) =>
            o.isExpense &&
            o.entityId == 'e-synced' &&
            o.op == BudgetsOutboxOp.delete),
        isTrue,
      );
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

    test('stamps created_at on the stored + returned expense', () async {
      const fixedNow = '2026-06-07T14:32:00.000Z';
      final dao = await _freshDao(now: () => fixedNow);
      final exp = await dao.applyLocalExpenseCreate('p1', 7.0, 'Coffee');

      // Returned model carries the stamped created_at (re-read from the row).
      expect(exp.createdAt, fixedNow);
      // And it survives a fresh read back out of the cache.
      final stored = await dao.getExpense(exp.id);
      expect(stored!.createdAt, fixedNow);
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

  group('BudgetsDao expense favorite (per-expense starring)', () {
    test('a freshly created expense defaults isFavorite=false', () async {
      final dao = await _freshDao();
      final exp = await dao.applyLocalExpenseCreate('p1', 5.0, 'Coffee');
      final stored = await dao.getExpense(exp.id);
      expect(stored!.isFavorite, isFalse);
    });

    test(
        'applyLocalExpenseFavorite sets the flag, marks dirty + enqueues an '
        'update op carrying is_favorite as a real JSON bool', () async {
      final dao = await _freshDao();
      final exp = await dao.applyLocalExpenseCreate('p1', 5.0, 'Coffee');
      // Drain the create so we isolate the favorite update op.
      final createSeq = (await dao.readBudgetsOutbox()).first.seq;
      await dao.commitPush(createSeq, kExpenseEntity, exp.id);
      expect(await dao.readBudgetsOutbox(), isEmpty);

      final updated = await dao.applyLocalExpenseFavorite(exp.id, true);
      expect(updated, isNotNull);
      expect(updated!.isFavorite, isTrue);

      // The stored row reflects the star + is dirty again.
      final stored = await dao.getExpense(exp.id);
      expect(stored!.isFavorite, isTrue);
      expect(await dao.dirtyExpenseIds(), contains(exp.id));

      // One queued update op targeting the expense, with a REAL bool payload.
      final outbox = await dao.readBudgetsOutbox();
      expect(outbox, hasLength(1));
      final op = outbox.single;
      expect(op.op, BudgetsOutboxOp.update);
      expect(op.entity, kExpenseEntity);
      expect(op.entityId, exp.id);
      expect(op.payload['id'], exp.id);
      expect(op.payload['is_favorite'], isTrue);
      expect(op.payload['is_favorite'], isA<bool>());
    });

    test('applyLocalExpenseFavorite can un-star (false) an expense', () async {
      final dao = await _freshDao();
      final exp = await dao.applyLocalExpenseCreate('p1', 5.0, 'Coffee');
      await dao.applyLocalExpenseFavorite(exp.id, true);
      final off = await dao.applyLocalExpenseFavorite(exp.id, false);
      expect(off!.isFavorite, isFalse);
      expect((await dao.getExpense(exp.id))!.isFavorite, isFalse);
      final lastOp = (await dao.readBudgetsOutbox()).last;
      expect(lastOp.payload['is_favorite'], isFalse);
    });

    test('applyLocalExpenseFavorite on a missing expense is a no-op', () async {
      final dao = await _freshDao();
      expect(await dao.applyLocalExpenseFavorite('nope', true), isNull);
      expect(await dao.readBudgetsOutbox(), isEmpty);
    });

    test('is_favorite round-trips through a server upsert (row ↔ model)',
        () async {
      final dao = await _freshDao();
      await dao.upsertExpenseFromServer(
        _serverExpense(id: 'srvfav').copyWith(isFavorite: true),
        serverUpdatedAt: '2026-06-05T11:00:00Z',
      );
      final stored = await dao.getExpense('srvfav');
      expect(stored!.isFavorite, isTrue);
    });
  });

  group('BudgetsDao expense delete', () {
    test('delete tombstones + enqueues a delete (server-backed row)', () async {
      final dao = await _freshDao();
      // A server-backed expense (last_synced_at set): its removal must reach the
      // server as a tombstone + delete op. (An un-synced create is collapsed
      // instead — see the "create/delete collapse" group.)
      await dao.upsertExpenseFromServer(_serverExpense(id: 'e-srv'));
      final ok = await dao.applyLocalExpenseDelete('e-srv');
      expect(ok, isTrue);

      expect(await dao.getExpense('e-srv'), isNotNull); // tombstone present
      expect(
          (await dao.listExpenses()).map((x) => x.id), isNot(contains('e-srv')));

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
      // Server-backed row so delete tombstones + enqueues a delete (an un-synced
      // create is collapsed instead, leaving no delete op to push).
      await dao.upsertExpenseFromServer(_serverExpense(id: 'cpe'));
      await dao.applyLocalExpenseDelete('cpe');
      final delSeq = (await dao.readBudgetsOutbox())
          .firstWhere((o) => o.op == BudgetsOutboxOp.delete)
          .seq;
      await dao.commitPush(delSeq, kExpenseEntity, 'cpe');
      expect(await dao.getExpense('cpe'), isNull);
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

    test('preserves the server created_at on a pull upsert', () async {
      final dao = await _freshDao();
      await dao.upsertExpenseFromServer(
        _serverExpense(id: 'srv').copyWith(createdAt: '2026-06-01T08:00:00Z'),
        serverUpdatedAt: '2026-06-05T11:00:00Z',
      );
      final stored = await dao.getExpense('srv');
      expect(stored!.createdAt, '2026-06-01T08:00:00Z');
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

  group('BudgetsDao budget-entry ledger (server pull)', () {
    test('upsertBudgetEntryFromServer writes a clean synced row', () async {
      final dao = await _freshDao();
      await dao.upsertBudgetEntryFromServer(
        _serverBudgetEntry(id: 'srv', amount: 300, source: 'Deposit'),
        serverUpdatedAt: '2026-07-02T09:00:00Z',
      );
      final entries = await dao.listBudgetEntries();
      expect(entries, hasLength(1));
      expect(entries.single.id, 'srv');
      expect(entries.single.amount, 300);
      expect(entries.single.source, 'Deposit');
      // Clean (server-authoritative) → not dirty, updated_at is the server one.
      expect(await dao.dirtyBudgetEntryIds(), isEmpty);
      final row = await dao.getBudgetEntryRow('srv');
      expect(row!['updated_at'], '2026-07-02T09:00:00Z');
      expect(row['dirty'], 0);
      expect(row['deleted'], 0);
      expect(row['last_synced_at'], isNotNull);
    });

    test('listBudgetEntries filters by projectId and orders newest-first',
        () async {
      final dao = await _freshDao();
      await dao.upsertBudgetEntryFromServer(_serverBudgetEntry(
          id: 'a', projectId: 'p1', createdAt: '2026-07-01T10:00:00Z'));
      await dao.upsertBudgetEntryFromServer(_serverBudgetEntry(
          id: 'b', projectId: 'p1', createdAt: '2026-07-03T10:00:00Z'));
      await dao.upsertBudgetEntryFromServer(_serverBudgetEntry(
          id: 'c', projectId: 'p2', createdAt: '2026-07-02T10:00:00Z'));

      // created_at DESC: b (07-03), c (07-02), a (07-01).
      final all = await dao.listBudgetEntries();
      expect(all.map((e) => e.id), ['b', 'c', 'a']);

      final p1 = await dao.listBudgetEntries(projectId: 'p1');
      expect(p1.map((e) => e.id), ['b', 'a']); // c is p2, excluded
    });

    test('applyServerBudgetEntryDelete tombstones the row (excluded from list)',
        () async {
      final dao = await _freshDao();
      await dao.upsertBudgetEntryFromServer(_serverBudgetEntry(id: 'srv'));
      await dao.applyServerBudgetEntryDelete('srv');
      expect((await dao.listBudgetEntries()).map((e) => e.id),
          isNot(contains('srv')));
    });

    test('applyServerBudgetEntryDelete is idempotent when the row is absent',
        () async {
      final dao = await _freshDao();
      // Must not throw on a delete for an id we never cached.
      await dao.applyServerBudgetEntryDelete('ghost');
      expect(await dao.listBudgetEntries(), isEmpty);
    });
  });

  group('BudgetsDao budget-entry ledger (local write)', () {
    test(
        'applyLocalBudgetEntryCreate: dirty row + create op + optimistic budget '
        'bump', () async {
      final dao = await _freshDao();
      // Seed a synced project with a known budget so the bump is observable.
      await dao.upsertProjectFromServer(_serverProject(id: 'p1', budget: 1000));

      final entry = await dao.applyLocalBudgetEntryCreate(
        'p1',
        200,
        source: 'Deposit',
        currency: 'USD',
      );

      // Row is present, dirty (un-pushed), never-synced.
      final row = await dao.getBudgetEntryRow(entry.id);
      expect(row, isNotNull);
      expect(row!['dirty'], 1);
      expect(row['last_synced_at'], isNull);
      expect(await dao.dirtyBudgetEntryIds(), contains(entry.id));
      expect((await dao.listBudgetEntries(projectId: 'p1')).single.source,
          'Deposit');

      // A create op is queued for the budget_entry entity.
      final ops = await dao.readBudgetsOutbox();
      final createOp = ops.singleWhere((o) => o.isBudgetEntry);
      expect(createOp.op, BudgetsOutboxOp.create);
      expect(createOp.entityId, entry.id);
      expect(createOp.payload['amount'], 200);

      // Optimistic bump so the budget bar moves offline (server derives the
      // real total from the entry, so NO project op is queued → no double-count).
      expect((await dao.getProject('p1'))!.budget, 1200);
      expect(await dao.dirtyProjectIds(), isEmpty,
          reason: 'the optimistic bump must NOT mark the project dirty');
    });

    test(
        'applyLocalBudgetEntryDelete on a never-synced create annihilates it + '
        'un-bumps the budget (net zero, nothing queued)', () async {
      final dao = await _freshDao();
      await dao.upsertProjectFromServer(_serverProject(id: 'p1', budget: 1000));
      final entry =
          await dao.applyLocalBudgetEntryCreate('p1', 200, currency: 'USD');
      expect((await dao.getProject('p1'))!.budget, 1200);

      await dao.applyLocalBudgetEntryDelete(entry.id);

      expect(await dao.getBudgetEntryRow(entry.id), isNull); // hard-removed
      expect(await dao.readBudgetsOutbox(), isEmpty); // create op cancelled
      expect((await dao.getProject('p1'))!.budget, 1000); // bump rolled back
    });

    test(
        'applyLocalBudgetEntryDelete on a synced entry tombstones + queues a '
        'delete + un-bumps the budget', () async {
      final dao = await _freshDao();
      // Server already reflects the +200 in the project budget (1200).
      await dao.upsertProjectFromServer(_serverProject(id: 'p1', budget: 1200));
      await dao.upsertBudgetEntryFromServer(
        _serverBudgetEntry(id: 'be1', projectId: 'p1', amount: 200),
        serverUpdatedAt: '2026-07-01T10:00:00Z',
      );

      await dao.applyLocalBudgetEntryDelete('be1');

      final row = await dao.getBudgetEntryRow('be1');
      expect(row!['deleted'], 1);
      expect(row['dirty'], 1);
      expect((await dao.listBudgetEntries()).map((e) => e.id),
          isNot(contains('be1')));
      final ops = await dao.readBudgetsOutbox();
      final delOp = ops.singleWhere((o) => o.isBudgetEntry);
      expect(delOp.op, BudgetsOutboxOp.delete);
      // Optimistic rollback so the bar drops immediately.
      expect((await dao.getProject('p1'))!.budget, 1000);
    });

    test('outboxCount + readBudgetsOutbox include budget_entry ops', () async {
      final dao = await _freshDao();
      await dao.upsertProjectFromServer(_serverProject(id: 'p1', budget: 0));
      await dao.applyLocalBudgetEntryCreate('p1', 50, currency: 'USD');
      expect(await dao.outboxCount(), 1);
      expect((await dao.readBudgetsOutbox()).single.isBudgetEntry, isTrue);
    });

    test('reenqueueOrphanedCreates re-enqueues a stranded ledger create',
        () async {
      final dao = await _freshDao();
      await dao.upsertProjectFromServer(_serverProject(id: 'p1', budget: 0));
      final entry =
          await dao.applyLocalBudgetEntryCreate('p1', 75, currency: 'USD');
      // Simulate the create op being dropped (dead-lettered / drained by an old
      // build) — the dirty, never-synced row is now orphaned.
      await dao.deleteOutboxForEntity(kBudgetEntryEntity, entry.id);
      expect(await dao.readBudgetsOutbox(), isEmpty);

      final healed = await dao.reenqueueOrphanedCreates();
      expect(healed, 1);
      final ops = await dao.readBudgetsOutbox();
      expect(ops.single.isBudgetEntry, isTrue);
      expect(ops.single.op, BudgetsOutboxOp.create);
      expect(ops.single.entityId, entry.id);
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
