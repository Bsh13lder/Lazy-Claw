// Unit tests for the BudgetsNotifier project mutators:
//   createProject / renameProject / setProjectColor / deleteProject.
//
// A recording BudgetsDao subclass captures the exact args forwarded to the DAO
// (the notifier is the seam under test, not the DB), proving:
//   * each mutator forwards the right args to the matching DAO method,
//   * it refreshes the visible project list afterwards (listProjects re-read),
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
/// sync becomes a deterministic no-op so the test only observes the mutator.
class _NoopSync extends BudgetsSync {
  _NoopSync(super.dao, super.repo);
  @override
  Future<BudgetsSyncResult> sync({bool retryRejected = false}) async => const BudgetsSyncResult();
}

/// BudgetsDao that records project-mutator calls and serves canned reads,
/// never hitting the real DB. The `throwOn*` flags exercise the error paths.
class _RecordingDao extends BudgetsDao {
  _RecordingDao(super.db);

  bool throwOnCreate = false;
  bool throwOnUpdate = false;
  bool throwOnDelete = false;

  final List<Map<String, dynamic>> createCalls = [];
  final List<Map<String, dynamic>> updateCalls = [];
  final List<String> deleteCalls = [];
  int listProjectCalls = 0;

  @override
  Future<Project> applyLocalProjectCreate(
    String name, {
    String? id,
    double? budget,
    String currency = 'USD',
    String? description,
    String? color,
    String? startDate,
    String? dueDate,
  }) async {
    createCalls.add({
      'name': name,
      'budget': budget,
      'color': color,
      'startDate': startDate,
      'dueDate': dueDate,
    });
    if (throwOnCreate) throw StateError('create boom');
    return Project(
      id: id ?? 'rec-id',
      name: name,
      budget: budget ?? 0.0,
      currency: currency,
      status: 'active',
      color: color,
      startDate: startDate,
      dueDate: dueDate,
    );
  }

  @override
  Future<Project?> applyLocalProjectUpdate(
    String id, {
    String? name,
    double? budget,
    String? description,
    String? status,
    String? color,
    bool? isFavorite,
    String? startDate,
    String? dueDate,
  }) async {
    updateCalls.add({
      'id': id,
      'name': name,
      'budget': budget,
      'description': description,
      'status': status,
      'color': color,
      'isFavorite': isFavorite,
      'startDate': startDate,
      'dueDate': dueDate,
    });
    if (throwOnUpdate) throw StateError('update boom');
    return null;
  }

  @override
  Future<bool> applyLocalProjectDelete(String id) async {
    deleteCalls.add(id);
    if (throwOnDelete) throw StateError('delete boom');
    return true;
  }

  @override
  Future<List<Project>> listProjects() async {
    listProjectCalls++;
    return const [];
  }

  @override
  Future<List<Expense>> listExpenses() async => const [];
  @override
  Future<Set<String>> dirtyProjectIds() async => const {};
  @override
  Future<Set<String>> dirtyExpenseIds() async => const {};
}

int _dbCounter = 0;

Future<_RecordingDao> _freshDao() async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:projcrudmem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return _RecordingDao(db);
}

BudgetsNotifier _notifier(_RecordingDao dao) => BudgetsNotifier(
      dao,
      _NoopSync(dao, BudgetsRepository(_OfflineTransport())),
    );

void main() {
  setUpAll(() => sqfliteFfiInit());

  group('BudgetsNotifier.createProject', () {
    test('forwards name + budget + color and refreshes', () async {
      final dao = await _freshDao();
      final n = _notifier(dao);

      final ok = await n.createProject('Marketing',
          budget: 1000.0, color: '#3DD68C');
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(ok, isTrue);
      expect(dao.createCalls, hasLength(1));
      final call = dao.createCalls.single;
      expect(call['name'], 'Marketing');
      expect(call['budget'], 1000.0);
      expect(call['color'], '#3DD68C');
      expect(dao.listProjectCalls, greaterThanOrEqualTo(1));
      expect(n.state.error, isNull);
    });

    test('color is optional (null when omitted)', () async {
      final dao = await _freshDao();
      final n = _notifier(dao);

      await n.createProject('Plain');
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(dao.createCalls.single['color'], isNull);
    });

    test('threads the time frame (startDate/dueDate) through to the DAO',
        () async {
      final dao = await _freshDao();
      final n = _notifier(dao);

      final ok = await n.createProject('Timed',
          startDate: '2026-08-01', dueDate: '2026-09-15');
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(ok, isTrue);
      final call = dao.createCalls.single;
      expect(call['startDate'], '2026-08-01');
      expect(call['dueDate'], '2026-09-15');
    });

    test('sets state.error when the DAO throws', () async {
      final dao = await _freshDao()..throwOnCreate = true;
      final n = _notifier(dao);

      final ok = await n.createProject('Boom');
      expect(ok, isFalse);
      expect(n.state.error, contains('create boom'));
    });
  });

  group('BudgetsNotifier.renameProject', () {
    test('forwards the new name to applyLocalProjectUpdate', () async {
      final dao = await _freshDao();
      final n = _notifier(dao);

      await n.renameProject('p1', 'New name');
      await Future<void>.delayed(const Duration(milliseconds: 20));

      final call = dao.updateCalls.single;
      expect(call['id'], 'p1');
      expect(call['name'], 'New name');
      expect(call['color'], isNull);
      expect(n.state.error, isNull);
    });

    test('sets state.error when the DAO throws', () async {
      final dao = await _freshDao()..throwOnUpdate = true;
      final n = _notifier(dao);

      await n.renameProject('p1', 'x');
      expect(n.state.error, contains('update boom'));
    });
  });

  group('BudgetsNotifier.setProjectDates', () {
    test('forwards startDate/dueDate (incl. the "" clear sentinel) to '
        'applyLocalProjectUpdate', () async {
      final dao = await _freshDao();
      final n = _notifier(dao);

      final ok = await n.setProjectDates('p1',
          startDate: '2026-08-01', dueDate: '');
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(ok, isTrue);
      final call = dao.updateCalls.single;
      expect(call['id'], 'p1');
      expect(call['startDate'], '2026-08-01');
      expect(call['dueDate'], '');
      expect(call['name'], isNull);
      expect(n.state.error, isNull);
    });

    test('sets state.error when the DAO throws', () async {
      final dao = await _freshDao()..throwOnUpdate = true;
      final n = _notifier(dao);

      final ok = await n.setProjectDates('p1', dueDate: '2026-09-15');
      expect(ok, isFalse);
      expect(n.state.error, contains('update boom'));
    });
  });

  group('BudgetsNotifier.setProjectColor', () {
    test('forwards the color to applyLocalProjectUpdate', () async {
      final dao = await _freshDao();
      final n = _notifier(dao);

      await n.setProjectColor('p1', '#5AA9FF');
      await Future<void>.delayed(const Duration(milliseconds: 20));

      final call = dao.updateCalls.single;
      expect(call['id'], 'p1');
      expect(call['color'], '#5AA9FF');
      expect(call['name'], isNull);
      expect(n.state.error, isNull);
    });

    test('sets state.error when the DAO throws', () async {
      final dao = await _freshDao()..throwOnUpdate = true;
      final n = _notifier(dao);

      await n.setProjectColor('p1', '#5AA9FF');
      expect(n.state.error, contains('update boom'));
    });
  });

  group('BudgetsNotifier.deleteProject', () {
    test('forwards the id to applyLocalProjectDelete + refreshes', () async {
      final dao = await _freshDao();
      final n = _notifier(dao);

      await n.deleteProject('p1');
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(dao.deleteCalls, contains('p1'));
      expect(dao.listProjectCalls, greaterThanOrEqualTo(1));
      expect(n.state.error, isNull);
    });

    test('sets state.error when the DAO throws', () async {
      final dao = await _freshDao()..throwOnDelete = true;
      final n = _notifier(dao);

      await n.deleteProject('p1');
      expect(n.state.error, contains('delete boom'));
    });
  });
}
