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
  });
}
