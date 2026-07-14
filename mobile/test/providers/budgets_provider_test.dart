import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/budgets_dao.dart';
import 'package:lazyclaw_mobile/providers/budgets_provider.dart';
import 'package:lazyclaw_mobile/repositories/budgets_repository.dart';
import 'package:lazyclaw_mobile/sync/budgets_sync.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

/// Transport that always fails the network — proves the provider works fully
/// offline (writes land locally + dirty, list reads from cache).
class _OfflineTransport implements BudgetsTransport {
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
  Future<Map<String, dynamic>> deleteJson(String path) async =>
      throw ApiError(0, 'offline');
}

int _dbCounter = 0;

Future<BudgetsDao> _freshDao() async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:budgetprovmem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return BudgetsDao(db);
}

void main() {
  setUpAll(() => sqfliteFfiInit());

  group('BudgetsNotifier offline-first', () {
    test('createProject writes to cache + marks dirty while offline', () async {
      final dao = await _freshDao();
      final sync = BudgetsSync(dao, BudgetsRepository(_OfflineTransport()));
      final n = BudgetsNotifier(dao, sync);

      await n.createProject('Marketing', budget: 1000.0);
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(n.state.projects.map((p) => p.name), contains('Marketing'));
      final created =
          n.state.projects.firstWhere((p) => p.name == 'Marketing');
      expect(n.state.dirtyProjectIds, contains(created.id));
    });

    test(
        'creditProjectBudget bumps the local budget by the delta + queues it '
        '(offline-safe Add-Budget fallback; additive, survives coalescing)',
        () async {
      final dao = await _freshDao();
      final sync = BudgetsSync(dao, BudgetsRepository(_OfflineTransport()));
      final n = BudgetsNotifier(dao, sync);

      await n.createProject('ClubBay', budget: 1000.0);
      await Future<void>.delayed(const Duration(milliseconds: 20));
      final proj = n.state.projects.firstWhere((p) => p.name == 'ClubBay');

      final ok = await n.creditProjectBudget(proj.id, 500.0);
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(ok, isTrue);
      expect(
          n.state.projects.firstWhere((p) => p.id == proj.id).budget, 1500.0);
      expect(n.state.dirtyProjectIds, contains(proj.id));

      // A second offline top-up reads the running local total → additive, so two
      // queued credits don't collapse to just the last one under LWW coalescing.
      await n.creditProjectBudget(proj.id, 250.0);
      await Future<void>.delayed(const Duration(milliseconds: 20));
      expect(
          n.state.projects.firstWhere((p) => p.id == proj.id).budget, 1750.0);
    });

    test('addExpense writes to cache + marks dirty while offline', () async {
      final dao = await _freshDao();
      final sync = BudgetsSync(dao, BudgetsRepository(_OfflineTransport()));
      final n = BudgetsNotifier(dao, sync);

      await n.createProject('Proj');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      final projectId = n.state.projects.first.id;

      await n.addExpense(projectId, 25.0, 'Lunch');
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(n.state.expenses.map((e) => e.description), contains('Lunch'));
      final exp = n.state.expenses.firstWhere((e) => e.description == 'Lunch');
      expect(n.state.dirtyExpenseIds, contains(exp.id));
      // The optimistic row carries the project name for the tile subtitle.
      expect(exp.projectName, 'Proj');
    });

    test(
        'toggleExpenseFavorite flips the star, persists + marks dirty offline',
        () async {
      final dao = await _freshDao();
      final sync = BudgetsSync(dao, BudgetsRepository(_OfflineTransport()));
      final n = BudgetsNotifier(dao, sync);

      await n.createProject('Proj');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      await n.addExpense(n.state.projects.first.id, 5.0, 'Latte');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      final id = n.state.expenses.first.id;
      expect(n.state.expenses.first.isFavorite, isFalse);

      // Star it.
      final ok = await n.toggleExpenseFavorite(id);
      await Future<void>.delayed(const Duration(milliseconds: 20));
      expect(ok, isTrue);
      expect(n.state.expenses.firstWhere((e) => e.id == id).isFavorite, isTrue);
      expect(n.state.dirtyExpenseIds, contains(id));
      // It persisted to the cache, not just the in-memory state.
      expect((await dao.getExpense(id))!.isFavorite, isTrue);

      // Un-star it.
      await n.toggleExpenseFavorite(id);
      await Future<void>.delayed(const Duration(milliseconds: 20));
      expect(n.state.expenses.firstWhere((e) => e.id == id).isFavorite, isFalse);
    });

    test('toggleExpenseFavorite on an unknown id is a no-op (false)', () async {
      final dao = await _freshDao();
      final sync = BudgetsSync(dao, BudgetsRepository(_OfflineTransport()));
      final n = BudgetsNotifier(dao, sync);
      expect(await n.toggleExpenseFavorite('nope'), isFalse);
    });

    test('removeExpense drops it from the visible list offline', () async {
      final dao = await _freshDao();
      final sync = BudgetsSync(dao, BudgetsRepository(_OfflineTransport()));
      final n = BudgetsNotifier(dao, sync);

      await n.createProject('Proj');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      await n.addExpense(n.state.projects.first.id, 5.0, 'Remove me');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      final id = n.state.expenses.first.id;

      await n.removeExpense(id);
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(n.state.expenses.map((e) => e.id), isNot(contains(id)));
    });

    test(
        'removeExpense drops it from state SYNCHRONOUSLY (before the DB await) '
        '— Dismissible-safe', () async {
      final dao = await _freshDao();
      final sync = BudgetsSync(dao, BudgetsRepository(_OfflineTransport()));
      final n = BudgetsNotifier(dao, sync);

      await n.createProject('Proj');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      await n.addExpense(n.state.projects.first.id, 5.0, 'Reserva');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      final id = n.state.expenses.first.id;

      // Call WITHOUT awaiting: the synchronous prefix must already have removed
      // the row from state, so a swipe's Dismissible.onDismissed never leaves a
      // dismissed tile in the tree ("A dismissed Dismissible widget is still
      // part of the tree" → screen freeze). With an await-first implementation
      // the row is still present here and this fails.
      final future = n.removeExpense(id);
      expect(
        n.state.expenses.map((e) => e.id),
        isNot(contains(id)),
        reason: 'row must leave state synchronously, before the DB await',
      );
      await future;
      expect(n.state.expenses.map((e) => e.id), isNot(contains(id)));
    });

    test(
        'deleteProject drops it from state SYNCHRONOUSLY (before the DB await) '
        '— Dismissible-safe', () async {
      final dao = await _freshDao();
      final sync = BudgetsSync(dao, BudgetsRepository(_OfflineTransport()));
      final n = BudgetsNotifier(dao, sync);

      await n.createProject('Kill me');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      final id = n.state.projects.first.id;

      final future = n.deleteProject(id);
      expect(
        n.state.projects.map((p) => p.id),
        isNot(contains(id)),
        reason: 'project must leave state synchronously, before the DB await',
      );
      await future;
      expect(n.state.projects.map((p) => p.id), isNot(contains(id)));
    });

    test('deleteProject drops it from the visible list offline', () async {
      final dao = await _freshDao();
      final sync = BudgetsSync(dao, BudgetsRepository(_OfflineTransport()));
      final n = BudgetsNotifier(dao, sync);

      await n.createProject('Remove me');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      final id = n.state.projects.first.id;

      await n.deleteProject(id);
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(n.state.projects.map((p) => p.id), isNot(contains(id)));
    });

    test('load reads from cache instantly even with the server down', () async {
      final dao = await _freshDao();
      await dao.applyLocalProjectCreate('Pre-existing', id: 'pre1');
      final sync = BudgetsSync(dao, BudgetsRepository(_OfflineTransport()));
      final n = BudgetsNotifier(dao, sync);

      await n.load();
      expect(n.state.projects.map((p) => p.name), contains('Pre-existing'));
      expect(n.state.isLoading, isFalse);
    });

    test('outbox accumulates the offline mutations for later replay', () async {
      final dao = await _freshDao();
      final sync = BudgetsSync(dao, BudgetsRepository(_OfflineTransport()));
      final n = BudgetsNotifier(dao, sync);

      await n.createProject('A');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      final id = n.state.projects.first.id;
      await n.addExpense(id, 9.0, 'E');
      await Future<void>.delayed(const Duration(milliseconds: 20));

      final outbox = await dao.readBudgetsOutbox();
      expect(outbox.map((o) => o.entity),
          containsAll([kProjectEntity, kExpenseEntity]));
    });

    test(
        'addBudgetEntryLocal creates a ledger row + bumps the budget offline '
        '(no money lost, audit note kept)', () async {
      final dao = await _freshDao();
      final sync = BudgetsSync(dao, BudgetsRepository(_OfflineTransport()));
      final n = BudgetsNotifier(dao, sync);

      await n.createProject('Gig', budget: 1000.0);
      await Future<void>.delayed(const Duration(milliseconds: 20));
      final proj = n.state.projects.firstWhere((p) => p.name == 'Gig');

      final ok = await n.addBudgetEntryLocal(proj.id, 500.0, source: 'deposit');
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(ok, isTrue);
      // A real ledger row exists (the B5 gap — offline credit used to skip it).
      expect(n.state.budgetEntries, hasLength(1));
      expect(n.state.entriesForProject(proj.id).single.source, 'deposit');
      expect(n.state.dirtyBudgetEntryIds, hasLength(1));
      // Budget bar moved immediately (optimistic bump), no double-count risk
      // because no project op was queued.
      expect(n.state.projects.firstWhere((p) => p.id == proj.id).budget, 1500.0);
      final outbox = await dao.readBudgetsOutbox();
      expect(outbox.where((o) => o.isBudgetEntry), hasLength(1));
      expect(outbox.where((o) => o.isProject && o.op == BudgetsOutboxOp.update),
          isEmpty);
    });

    test('removeBudgetEntry drops it from state + rolls back the budget',
        () async {
      final dao = await _freshDao();
      final sync = BudgetsSync(dao, BudgetsRepository(_OfflineTransport()));
      final n = BudgetsNotifier(dao, sync);

      await n.createProject('Gig', budget: 1000.0);
      await Future<void>.delayed(const Duration(milliseconds: 20));
      final proj = n.state.projects.firstWhere((p) => p.name == 'Gig');
      await n.addBudgetEntryLocal(proj.id, 500.0);
      await Future<void>.delayed(const Duration(milliseconds: 20));
      final entryId = n.state.budgetEntries.single.id;
      expect(n.state.projects.firstWhere((p) => p.id == proj.id).budget, 1500.0);

      await n.removeBudgetEntry(entryId);
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(n.state.budgetEntries, isEmpty);
      expect(n.state.projects.firstWhere((p) => p.id == proj.id).budget, 1000.0);
    });
  });
}
