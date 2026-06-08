import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/budgets_dao.dart';
import 'package:lazyclaw_mobile/models/expense.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/providers/budgets_provider.dart';
import 'package:lazyclaw_mobile/repositories/budgets_repository.dart';
import 'package:lazyclaw_mobile/sync/budgets_sync.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

/// Always-offline transport — proves derivation works purely on the local cache
/// (the server `changes` feed never carries the spent/remaining rollup).
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
    'file:budgettotmem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return BudgetsDao(db);
}

BudgetsNotifier _notifier(BudgetsDao dao) =>
    BudgetsNotifier(dao, BudgetsSync(dao, BudgetsRepository(_OfflineTransport())));

Future<void> _settle() =>
    Future<void>.delayed(const Duration(milliseconds: 20));

void main() {
  setUpAll(() => sqfliteFfiInit());

  group('derived project spent/remaining (DAO listProjects)', () {
    test('a freshly synced project with no rollup derives spent=0', () async {
      final dao = await _freshDao();
      // Simulate a pull: the server `changes` feed never sends spent/remaining.
      await dao.upsertProjectFromServer(
        Project.fromJson({'id': 'p1', 'name': 'Synced', 'budget': 100.0}),
      );
      final projects = await dao.listProjects();
      expect(projects.single.spent, 0.0);
      expect(projects.single.remaining, 100.0);
    });

    test('spent = sum of non-void, non-deleted expenses', () async {
      final dao = await _freshDao();
      await dao.applyLocalProjectCreate('Proj', id: 'p1', budget: 100.0);
      await dao.applyLocalExpenseCreate('p1', 10.0, 'a');
      await dao.applyLocalExpenseCreate('p1', 20.0, 'b');
      // A void expense must NOT inflate spent.
      await dao.upsertExpenseFromServer(
        Expense.fromJson({
          'id': 'void1',
          'project_id': 'p1',
          'amount': 999.0,
          'status': 'void',
        }),
      );
      final spentMap = await dao.spentByProject();
      expect(spentMap['p1'], closeTo(30.0, 0.001));

      final p = (await dao.listProjects()).single;
      expect(p.spent, closeTo(30.0, 0.001));
      expect(p.remaining, closeTo(70.0, 0.001));
    });

    test('a deleted (tombstoned) expense is excluded', () async {
      final dao = await _freshDao();
      await dao.applyLocalProjectCreate('Proj', id: 'p1', budget: 100.0);
      final e = await dao.applyLocalExpenseCreate('p1', 40.0, 'keep');
      await dao.applyLocalExpenseCreate('p1', 25.0, 'drop me');
      // Delete the second one.
      final all = await dao.listExpenses();
      final drop = all.firstWhere((x) => x.description == 'drop me');
      await dao.applyLocalExpenseDelete(drop.id);

      final p = (await dao.listProjects()).single;
      expect(p.spent, closeTo(40.0, 0.001));
      expect(e.id, isNotEmpty);
    });
  });

  group('totals recompute live after add/edit/delete (notifier, offline)', () {
    test('addExpense bumps the project spent + lowers remaining', () async {
      final dao = await _freshDao();
      final n = _notifier(dao);
      await n.createProject('Proj', budget: 100.0);
      await _settle();
      final id = n.state.projects.single.id;

      await n.addExpense(id, 30.0, 'Lunch');
      await _settle();

      final p = n.state.projects.single;
      expect(p.spent, closeTo(30.0, 0.001));
      expect(p.remaining, closeTo(70.0, 0.001));
    });

    test('updateExpense amount recomputes the project spent', () async {
      final dao = await _freshDao();
      final n = _notifier(dao);
      await n.createProject('Proj', budget: 100.0);
      await _settle();
      final pid = n.state.projects.single.id;

      await n.addExpense(pid, 10.0, 'Coffee');
      await _settle();
      final exp = n.state.expenses.single;

      await n.updateExpense(exp.id, amount: 25.0, description: 'Coffee');
      await _settle();

      expect(n.state.projects.single.spent, closeTo(25.0, 0.001));
    });

    test('removeExpense recomputes the project spent', () async {
      final dao = await _freshDao();
      final n = _notifier(dao);
      await n.createProject('Proj', budget: 100.0);
      await _settle();
      final pid = n.state.projects.single.id;

      await n.addExpense(pid, 10.0, 'A');
      await _settle();
      await n.addExpense(pid, 20.0, 'B');
      await _settle();
      expect(n.state.projects.single.spent, closeTo(30.0, 0.001));

      final toRemove = n.state.expenses.firstWhere((e) => e.description == 'A');
      await n.removeExpense(toRemove.id);
      await _settle();

      expect(n.state.projects.single.spent, closeTo(20.0, 0.001));
    });

    test('a local expense inherits its project currency (not USD)', () async {
      final dao = await _freshDao();
      final n = _notifier(dao);
      // EUR project — created via DAO so we control the currency.
      await dao.applyLocalProjectCreate('EuroProj',
          id: 'eur1', budget: 100.0, currency: 'EUR');
      await n.load();
      await _settle();

      await n.addExpense('eur1', 12.0, 'Café');
      await _settle();

      final exp = n.state.expenses.single;
      expect(exp.currency, 'EUR');
    });
  });
}
