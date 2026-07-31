// Widget tests for the tap-an-expense detail/edit sheet.
//
// The sheet is opened via the public showExpenseDetailSheet helper (so the
// modal route + pop behaviour is exercised end-to-end) with budgetsProvider
// overridden by a stub notifier that records updateExpense / removeExpense
// invocations. The stub's BudgetsDao is backed by a noSuchMethod fake Database
// so no real sqflite isolate is spun up (its timer would hang pumpAndSettle
// under FakeAsync); the overridden methods never touch the DAO anyway.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
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
import 'package:sqflite_common/sqlite_api.dart';

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
  Future<BudgetsSyncResult> sync({bool retryRejected = false}) async => const BudgetsSyncResult();
}

/// Records the editor's writes without touching the DAO/network. Seeds two
/// projects so the in-sheet project picker has options to render.
class _StubBudgetsNotifier extends BudgetsNotifier {
  _StubBudgetsNotifier(super.dao, super.sync) {
    state = const BudgetsState(projects: [
      Project(
          id: 'proj-1',
          name: 'Marketing',
          budget: 0,
          currency: 'USD',
          status: 'active'),
      Project(
          id: 'proj-2',
          name: 'Operations',
          budget: 0,
          currency: 'USD',
          status: 'active'),
    ]);
  }

  final List<Map<String, dynamic>> updateCalls = [];
  final List<String> deleteCalls = [];

  @override
  Future<bool> updateExpense(
    String id, {
    double? amount,
    String? description,
    String? vendor,
    String? projectId,
    String? taskId,
    bool taskIdSet = false,
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
      'notes': notes,
      'spentAt': spentAt,
    });
    return true;
  }

  @override
  Future<void> removeExpense(String id) async => deleteCalls.add(id);
}

/// A Database that throws on any access. The stub notifier overrides every
/// method that would touch the DAO, so this is never actually invoked — it only
/// satisfies BudgetsDao's non-null constructor arg WITHOUT spinning up a real
/// sqflite isolate (whose timer would hang pumpAndSettle under FakeAsync).
class _FakeDatabase implements Database {
  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('Fake DB must not be touched in this test');
}

_StubBudgetsNotifier _stub() {
  final dao = BudgetsDao(_FakeDatabase());
  return _StubBudgetsNotifier(
    dao,
    _NoopSync(dao, BudgetsRepository(_OfflineTransport())),
  );
}

class _TasksOfflineTransport implements TasksTransport {
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
  Future<Map<String, dynamic>> putJson(
          String path, Map<String, dynamic> body) async =>
      throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async =>
      throw ApiError(0, 'offline');
}

/// Seeds the tasks list the in-sheet task picker reads via `tasksProvider`.
/// Never calls load()/sync — the sheet only reads `state.tasks` — so the
/// (also-fake) TaskDao/TaskSync backing it are never actually touched.
class _StubTasksNotifier extends TasksNotifier {
  _StubTasksNotifier(super.dao, super.sync, List<Task> tasks) {
    state = TasksState(tasks: tasks);
  }
}

Task _task(String id, String title, {String? category}) => Task(
      id: id,
      userId: 'u1',
      title: title,
      category: category,
      priority: 'medium',
      status: 'todo',
      owner: 'user',
      nagCount: 0,
      createdAt: '2026-06-06T00:00:00Z',
    );

_StubTasksNotifier _stubTasks([List<Task> tasks = const []]) {
  final dao = TaskDao(_FakeDatabase());
  return _StubTasksNotifier(
    dao,
    TaskSync(dao, TasksRepository(_TasksOfflineTransport())),
    tasks,
  );
}

const _sample = Expense(
  id: 'exp-42',
  projectId: 'proj-1',
  amount: 12.5,
  currency: 'USD',
  description: 'Coffee beans',
  vendor: 'Blue Bottle',
  status: 'posted',
  spentAt: '2026-06-05',
);

void main() {
  Widget host(
    _StubBudgetsNotifier stub, {
    List<Task> tasks = const [],
    Expense expense = _sample,
  }) =>
      ProviderScope(
        overrides: [
          budgetsProvider.overrideWith((ref) => stub),
          tasksProvider.overrideWith((ref) => _stubTasks(tasks)),
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

  Future<void> openSheet(
    WidgetTester tester,
    _StubBudgetsNotifier stub, {
    List<Task> tasks = const [],
    Expense expense = _sample,
  }) async {
    await tester.pumpWidget(host(stub, tasks: tasks, expense: expense));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
  }

  testWidgets('pre-fills amount, description and vendor from the expense',
      (tester) async {
    final stub = _stub();
    await openSheet(tester, stub);

    final amountField = tester
        .widget<TextField>(find.byKey(const Key('expense-detail-amount')));
    expect(amountField.controller!.text, '12.5');

    final descField =
        tester.widget<TextField>(find.byKey(const Key('expense-detail-desc')));
    expect(descField.controller!.text, 'Coffee beans');

    final vendorField = tester
        .widget<TextField>(find.byKey(const Key('expense-detail-vendor')));
    expect(vendorField.controller!.text, 'Blue Bottle');
  });

  testWidgets('editing the amount + tapping Save invokes updateExpense',
      (tester) async {
    final stub = _stub();
    await openSheet(tester, stub);

    await tester.enterText(
        find.byKey(const Key('expense-detail-amount')), '20');
    await tester.enterText(
        find.byKey(const Key('expense-detail-desc')), 'Edited');
    await tester.ensureVisible(find.byKey(const Key('expense-detail-save')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('expense-detail-save')));
    await tester.pumpAndSettle();

    expect(stub.updateCalls, hasLength(1));
    expect(stub.updateCalls.single['id'], 'exp-42');
    expect(stub.updateCalls.single['amount'], 20.0);
    expect(stub.updateCalls.single['description'], 'Edited');
    // No task was ever linked/selected — Save still submits taskIdSet: true
    // (the picker's current value, "(no task)", is an explicit clear/no-op).
    expect(stub.updateCalls.single['taskId'], isNull);
    expect(stub.updateCalls.single['taskIdSet'], isTrue);
    // The sheet closed after saving.
    expect(find.byKey(const Key('expense-detail-amount')), findsNothing);
  });

  testWidgets(
      'pre-fills the linked task when it belongs to the expense\'s project',
      (tester) async {
    final stub = _stub();
    const withTask = Expense(
      id: 'exp-77',
      projectId: 'proj-1',
      taskId: 't1',
      amount: 5,
      currency: 'USD',
      description: 'Snack',
      status: 'posted',
    );
    await openSheet(
      tester,
      stub,
      tasks: [
        _task('t1', 'Book flights', category: 'Marketing'),
        _task('t2', 'Ops review', category: 'Operations'),
      ],
      expense: withTask,
    );

    final taskDropdown = tester
        .widget<DropdownButton<String>>(find.byKey(const Key('expense-detail-task')));
    expect(taskDropdown.value, 't1');

    await tester.ensureVisible(find.byKey(const Key('expense-detail-save')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('expense-detail-save')));
    await tester.pumpAndSettle();

    expect(stub.updateCalls.single['taskId'], 't1');
    expect(stub.updateCalls.single['taskIdSet'], isTrue);
  });

  testWidgets('changing the project resets the task selection to null',
      (tester) async {
    final stub = _stub();
    const withTask = Expense(
      id: 'exp-78',
      projectId: 'proj-1',
      taskId: 't1',
      amount: 5,
      currency: 'USD',
      description: 'Snack',
      status: 'posted',
    );
    await openSheet(
      tester,
      stub,
      tasks: [_task('t1', 'Book flights', category: 'Marketing')],
      expense: withTask,
    );

    // Sanity: the task starts pre-filled from the expense.
    expect(
      tester
          .widget<DropdownButton<String>>(
              find.byKey(const Key('expense-detail-project')))
          .value,
      'proj-1',
    );
    expect(
      tester
          .widget<DropdownButton<String>>(find.byKey(const Key('expense-detail-task')))
          .value,
      't1',
    );

    // Open the project dropdown and pick the other project.
    await tester.tap(find.byKey(const Key('expense-detail-project')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Operations').last);
    await tester.pumpAndSettle();

    expect(
      tester
          .widget<DropdownButton<String>>(
              find.byKey(const Key('expense-detail-project')))
          .value,
      'proj-2',
    );
    // The task link belonged to the old project — it must reset, not carry
    // over onto the new one.
    expect(
      tester
          .widget<DropdownButton<String>>(find.byKey(const Key('expense-detail-task')))
          .value,
      isNull,
    );
  });

  testWidgets('Delete asks to confirm then invokes removeExpense',
      (tester) async {
    final stub = _stub();
    await openSheet(tester, stub);

    await tester.ensureVisible(find.byKey(const Key('expense-detail-delete')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('expense-detail-delete')));
    await tester.pumpAndSettle();

    // Confirmation dialog is up; nothing deleted yet.
    expect(stub.deleteCalls, isEmpty);

    // Tap the dialog's "Delete" confirm (footer button reads "Delete Expense").
    await tester.tap(find.text('Delete'));
    await tester.pumpAndSettle();

    expect(stub.deleteCalls, ['exp-42']);
    expect(find.byKey(const Key('expense-detail-amount')), findsNothing);
  });
}
