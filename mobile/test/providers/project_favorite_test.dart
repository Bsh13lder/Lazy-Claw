// Unit tests for the BudgetsNotifier favorite + budget mutators:
//   toggleFavorite(id) / setProjectBudget(id, budget).
//
// Unlike project_crud_test (which records DAO args against a recording stub),
// toggleFavorite reads the CURRENT favorite value off the loaded notifier
// state, so these tests use a REAL in-memory FFI DAO seeded with a project and
// loaded into the notifier first. The sync is a deterministic no-op so the test
// only observes the optimistic local write.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/budgets_dao.dart';
import 'package:lazyclaw_mobile/providers/budgets_provider.dart';
import 'package:lazyclaw_mobile/repositories/budgets_repository.dart';
import 'package:lazyclaw_mobile/sync/budgets_sync.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

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

class _NoopSync extends BudgetsSync {
  _NoopSync(super.dao, super.repo);
  @override
  Future<BudgetsSyncResult> sync() async => const BudgetsSyncResult();
}

int _dbCounter = 0;

Future<BudgetsDao> _freshDao() async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:projfavprov${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return BudgetsDao(db);
}

BudgetsNotifier _notifier(BudgetsDao dao) => BudgetsNotifier(
      dao,
      _NoopSync(dao, BudgetsRepository(_OfflineTransport())),
    );

void main() {
  setUpAll(() => sqfliteFfiInit());

  group('BudgetsNotifier.toggleFavorite', () {
    test('flips an un-favorited project to favorited (state + cache)',
        () async {
      final dao = await _freshDao();
      final created = await dao.applyLocalProjectCreate('Marketing');
      final n = _notifier(dao);
      await n.load();
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(n.state.projects.single.isFavorite, isFalse);

      final ok = await n.toggleFavorite(created.id);
      expect(ok, isTrue);
      expect(n.state.projects.single.isFavorite, isTrue);

      final stored = await dao.getProject(created.id);
      expect(stored!.isFavorite, isTrue);
      expect(n.state.error, isNull);
    });

    test('flips a favorited project back off', () async {
      final dao = await _freshDao();
      final created = await dao.applyLocalProjectCreate('Marketing');
      await dao.applyLocalProjectUpdate(created.id, isFavorite: true);
      final n = _notifier(dao);
      await n.load();
      await Future<void>.delayed(const Duration(milliseconds: 20));
      expect(n.state.projects.single.isFavorite, isTrue);

      await n.toggleFavorite(created.id);
      expect(n.state.projects.single.isFavorite, isFalse);
    });

    test('returns false for an unknown project id (no state in list)',
        () async {
      final dao = await _freshDao();
      final n = _notifier(dao);
      await n.load();
      await Future<void>.delayed(const Duration(milliseconds: 20));

      final ok = await n.toggleFavorite('does-not-exist');
      expect(ok, isFalse);
    });
  });

  group('BudgetsNotifier.setProjectBudget', () {
    test('overwrites the project budget (state + cache)', () async {
      final dao = await _freshDao();
      final created =
          await dao.applyLocalProjectCreate('Marketing', budget: 100.0);
      final n = _notifier(dao);
      await n.load();
      await Future<void>.delayed(const Duration(milliseconds: 20));
      expect(n.state.projects.single.budget, 100.0);

      final ok = await n.setProjectBudget(created.id, 950.0);
      expect(ok, isTrue);
      expect(n.state.projects.single.budget, 950.0);

      final stored = await dao.getProject(created.id);
      expect(stored!.budget, 950.0);
      expect(n.state.error, isNull);
    });

    test('can clear the budget back to zero', () async {
      final dao = await _freshDao();
      final created =
          await dao.applyLocalProjectCreate('Marketing', budget: 500.0);
      final n = _notifier(dao);
      await n.load();
      await Future<void>.delayed(const Duration(milliseconds: 20));

      await n.setProjectBudget(created.id, 0.0);
      expect(n.state.projects.single.budget, 0.0);
    });
  });
}
