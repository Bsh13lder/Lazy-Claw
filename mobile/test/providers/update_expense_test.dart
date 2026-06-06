// Unit tests for BudgetsNotifier.updateExpense.
//
// A recording BudgetsDao subclass captures the exact named args forwarded to
// applyLocalExpenseUpdate (the notifier is the seam under test, not the DB),
// proving:
//   * updateExpense forwards the supplied fields to applyLocalExpenseUpdate,
//   * it refreshes the visible ledger afterwards (listExpenses re-read),
//   * a DAO throw is surfaced as state.error instead of escaping.
//
// The recording DAO is backed by a throwaway in-memory FFI database purely to
// satisfy BudgetsDao's non-null Database constructor arg; every overridden
// method short-circuits before touching it.

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

/// Transport that always fails the network — the notifier's fire-and-forget
/// best-effort sync stays fully offline so the test only observes the write.
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

/// Sync that never touches the network — the notifier's `unawaited` best-effort
/// sync becomes a deterministic no-op so the test only observes updateExpense.
class _NoopSync extends BudgetsSync {
  _NoopSync(super.dao, super.repo);
  @override
  Future<BudgetsSyncResult> sync() async => const BudgetsSyncResult();
}

/// BudgetsDao that records applyLocalExpenseUpdate calls and serves canned
/// reads, never hitting the real DB. [throwOnUpdate] exercises the error path.
class _RecordingDao extends BudgetsDao {
  _RecordingDao(super.db);

  bool throwOnUpdate = false;
  final List<Map<String, dynamic>> updateCalls = [];
  int listExpenseCalls = 0;

  @override
  Future<Expense?> applyLocalExpenseUpdate(
    String id, {
    double? amount,
    String? description,
    String? vendor,
    String? projectId,
    String? notes,
    String? spentAt,
  }) async {
    updateCalls.add({
      'id': id,
      'amount': amount,
      'description': description,
      'vendor': vendor,
      'projectId': projectId,
      'notes': notes,
      'spentAt': spentAt,
    });
    if (throwOnUpdate) throw StateError('db boom');
    return null;
  }

  @override
  Future<List<Project>> listProjects() async => const [];
  @override
  Future<List<Expense>> listExpenses() async {
    listExpenseCalls++;
    return const [];
  }

  @override
  Future<Set<String>> dirtyProjectIds() async => const {};
  @override
  Future<Set<String>> dirtyExpenseIds() async => const {};
}

int _dbCounter = 0;

Future<_RecordingDao> _freshDao() async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:updexpmem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return _RecordingDao(db);
}

void main() {
  setUpAll(() => sqfliteFfiInit());

  group('BudgetsNotifier.updateExpense', () {
    test(
        'forwards the supplied fields to applyLocalExpenseUpdate and refreshes',
        () async {
      final dao = await _freshDao();
      final n = BudgetsNotifier(
          dao, _NoopSync(dao, BudgetsRepository(_OfflineTransport())));

      await n.updateExpense(
        'exp-1',
        amount: 42.5,
        description: 'new desc',
        vendor: 'Acme',
        projectId: 'proj-9',
        notes: 'memo',
        spentAt: '2026-06-09',
      );
      // Let the unawaited best-effort sync settle.
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(dao.updateCalls, hasLength(1));
      final call = dao.updateCalls.single;
      expect(call['id'], 'exp-1');
      expect(call['amount'], 42.5);
      expect(call['description'], 'new desc');
      expect(call['vendor'], 'Acme');
      expect(call['projectId'], 'proj-9');
      expect(call['notes'], 'memo');
      expect(call['spentAt'], '2026-06-09');
      // The ledger was re-read at least once (refresh ran).
      expect(dao.listExpenseCalls, greaterThanOrEqualTo(1));
      expect(n.state.error, isNull);
    });

    test('only forwards the fields that were passed', () async {
      final dao = await _freshDao();
      final n = BudgetsNotifier(
          dao, _NoopSync(dao, BudgetsRepository(_OfflineTransport())));

      await n.updateExpense('exp-2', amount: 10.0);
      await Future<void>.delayed(const Duration(milliseconds: 20));

      final call = dao.updateCalls.single;
      expect(call['amount'], 10.0);
      expect(call['description'], isNull);
      expect(call['vendor'], isNull);
      expect(call['projectId'], isNull);
      expect(call['notes'], isNull);
      expect(call['spentAt'], isNull);
    });

    test('sets state.error when the DAO throws', () async {
      final dao = await _freshDao()..throwOnUpdate = true;
      final n = BudgetsNotifier(
          dao, _NoopSync(dao, BudgetsRepository(_OfflineTransport())));

      await n.updateExpense('exp-3', amount: 1.0);
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(n.state.error, isNotNull);
      expect(n.state.error, contains('db boom'));
    });
  });
}
