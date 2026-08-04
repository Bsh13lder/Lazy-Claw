// Shared widget-test harness for the EXPENSE detail (edit) sheet.
//
// NOT a `_test.dart` file: it holds no tests, only the provider stubs the
// expense-detail test files share.
//
// FakeAsync hazard (documented project-wide): `testWidgets` runs inside
// FakeAsync, so a real sqflite/SQLCipher call inside one never completes and
// hangs the test. Both notifiers here override every method that would reach a
// DAO, and the DAOs are handed a [FakeDatabase] that throws on ANY access — so
// a future edit that reintroduces a real DB call fails loudly instead of
// hanging.

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/budgets_dao.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/models/expense.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/providers/budgets_provider.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart';
import 'package:lazyclaw_mobile/repositories/budgets_repository.dart';
import 'package:lazyclaw_mobile/repositories/tasks_repository.dart';
import 'package:lazyclaw_mobile/screens/expenses/expense_detail_sheet.dart';
import 'package:lazyclaw_mobile/sync/budgets_sync.dart';
import 'package:lazyclaw_mobile/sync/task_sync.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:sqflite_sqlcipher/sqflite.dart';

/// A Database that throws on any access — see the FakeAsync note above.
class FakeDatabase implements Database {
  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('Fake DB must not be touched in this test');
}

class _OfflineBudgetsTransport implements BudgetsTransport {
  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async => throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  ) async => throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> patchJson(
    String path,
    Map<String, dynamic> body,
  ) async => throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async =>
      throw ApiError(0, 'offline');
}

class _OfflineTasksTransport implements TasksTransport {
  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async => throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  ) async => throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> patchJson(
    String path,
    Map<String, dynamic> body,
  ) async => throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> putJson(
    String path,
    Map<String, dynamic> body,
  ) async => throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async =>
      throw ApiError(0, 'offline');
}

class _NoopBudgetsSync extends BudgetsSync {
  _NoopBudgetsSync(super.dao, super.repo);
  @override
  Future<BudgetsSyncResult> sync({bool retryRejected = false}) async =>
      const BudgetsSyncResult();
}

/// Records the sheet's writes without touching the DAO or the network.
class StubBudgetsNotifier extends BudgetsNotifier {
  StubBudgetsNotifier(super.dao, super.sync, List<Project> projects) {
    state = BudgetsState(projects: projects);
  }

  final List<Map<String, dynamic>> updateCalls = [];
  final List<String> deleteCalls = [];

  /// When non-null, every [updateExpense] records its arguments then PARKS
  /// here until the test completes it — lets a test hold one write in flight
  /// and prove that edits landing during it are coalesced, not raced.
  Completer<void>? updateGate;

  @override
  Future<bool> updateExpense(
    String id, {
    double? amount,
    String? description,
    String? vendor,
    String? projectId,
    String? taskId,
    bool taskIdSet = false,
    String? subtaskId,
    bool subtaskIdSet = false,
    String? notes,
    String? spentAt,
  }) async {
    updateCalls.add({
      'id': id,
      'amount': amount,
      'description': description,
      'vendor': vendor,
      'projectId': projectId,
      'taskId': taskId,
      'taskIdSet': taskIdSet,
      'subtaskId': subtaskId,
      'subtaskIdSet': subtaskIdSet,
      'notes': notes,
      'spentAt': spentAt,
    });
    final gate = updateGate;
    if (gate != null) await gate.future;
    return true;
  }

  @override
  Future<void> removeExpense(String id) async => deleteCalls.add(id);
}

/// Seeds the tasks the in-sheet task picker reads. Never loads or syncs.
class StubTasksNotifier extends TasksNotifier {
  StubTasksNotifier(super.dao, super.sync, List<Task> tasks) {
    state = TasksState(tasks: tasks);
  }
}

const List<Project> kSampleProjects = [
  Project(
    id: 'proj-1',
    name: 'Marketing',
    budget: 0,
    currency: 'USD',
    status: 'active',
  ),
  Project(
    id: 'proj-2',
    name: 'Operations',
    budget: 0,
    currency: 'USD',
    status: 'active',
  ),
];

StubBudgetsNotifier makeExpenseBudgetsStub({
  List<Project> projects = kSampleProjects,
}) {
  final dao = BudgetsDao(FakeDatabase());
  return StubBudgetsNotifier(
    dao,
    _NoopBudgetsSync(dao, BudgetsRepository(_OfflineBudgetsTransport())),
    projects,
  );
}

StubTasksNotifier makeExpenseTasksStub([List<Task> tasks = const []]) {
  final dao = TaskDao(FakeDatabase());
  return StubTasksNotifier(
    dao,
    TaskSync(dao, TasksRepository(_OfflineTasksTransport())),
    tasks,
  );
}

Task makeLinkedTask(
  String id,
  String title, {
  String? category,
  String? steps,
  String status = 'todo',
}) => Task(
  id: id,
  userId: 'u1',
  title: title,
  category: category,
  priority: 'medium',
  status: status,
  owner: 'user',
  steps: steps,
  nagCount: 0,
  createdAt: '2026-06-06T00:00:00Z',
);

/// A `MaterialApp` whose only button opens the expense detail sheet.
Widget expenseSheetHost({
  required StubBudgetsNotifier budgets,
  required StubTasksNotifier tasks,
  required Expense expense,
}) => ProviderScope(
  overrides: [
    budgetsProvider.overrideWith((ref) => budgets),
    tasksProvider.overrideWith((ref) => tasks),
  ],
  child: MaterialApp(
    theme: buildAppTheme(),
    home: Consumer(
      builder: (ctx, ref, _) => Scaffold(
        body: Center(
          child: ElevatedButton(
            onPressed: () => showExpenseDetailSheet(ctx, ref, expense),
            child: const Text('open'),
          ),
        ),
      ),
    ),
  ),
);
