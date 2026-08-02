// Unit tests for BudgetsNotifier.ensureProject — the case-insensitive
// get-or-create used when a task is linked to a project by name (Add Task's
// PROJECT chip + `/token` suggestions both route through this so a
// near-match casing never spawns a duplicate project).

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/budgets_dao.dart';
import 'package:lazyclaw_mobile/providers/budgets_provider.dart';
import 'package:lazyclaw_mobile/repositories/budgets_repository.dart';
import 'package:lazyclaw_mobile/sync/budgets_sync.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

/// Transport that always fails the network — proves ensureProject's
/// create path works fully offline (write lands locally + in the outbox).
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
    'file:budgetsensuremem${_dbCounter++}?mode=memory&cache=shared',
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

  group('BudgetsNotifier.ensureProject', () {
    test('unknown name creates a project + queues an outbox create op',
        () async {
      final dao = await _freshDao();
      final sync = BudgetsSync(dao, BudgetsRepository(_OfflineTransport()));
      final n = BudgetsNotifier(dao, sync);

      await n.ensureProject('Groceries');
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(n.state.projects.map((p) => p.name), contains('Groceries'));
      final outbox = await dao.readBudgetsOutbox();
      expect(
        outbox.where(
            (o) => o.isProject && o.op == BudgetsOutboxOp.create),
        hasLength(1),
      );
    });

    test(
        'existing name in different case is a no-op (no duplicate, no op '
        'queued)', () async {
      final dao = await _freshDao();
      final sync = BudgetsSync(dao, BudgetsRepository(_OfflineTransport()));
      final n = BudgetsNotifier(dao, sync);

      await n.createProject('Groceries');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      expect(n.state.projects, hasLength(1));

      await n.ensureProject('GROCERIES');
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(n.state.projects, hasLength(1));
      final outbox = await dao.readBudgetsOutbox();
      expect(
        outbox.where(
            (o) => o.isProject && o.op == BudgetsOutboxOp.create),
        hasLength(1),
        reason: 'only the original createProject call should have queued an '
            'op — ensureProject must not add a second one',
      );
    });

    test('empty/whitespace name is a no-op', () async {
      final dao = await _freshDao();
      final sync = BudgetsSync(dao, BudgetsRepository(_OfflineTransport()));
      final n = BudgetsNotifier(dao, sync);

      await n.ensureProject('');
      await n.ensureProject('   ');
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(n.state.projects, isEmpty);
      final outbox = await dao.readBudgetsOutbox();
      expect(outbox, isEmpty);
    });
  });
}
