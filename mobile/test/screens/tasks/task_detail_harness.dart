// Shared widget-test harness for the task detail sheet (money / tags /
// comments / floating-save coverage).
//
// NOT a `_test.dart` file on purpose — it holds no tests, only the provider
// stubs the D-series test files share so 150 lines of boilerplate isn't
// copy-pasted four times.
//
// FakeAsync hazard (documented project-wide): `testWidgets` runs inside
// FakeAsync, so a real sqflite/SQLCipher call inside one never completes and
// hangs the test. Every notifier here overrides the methods that would reach
// the DAO, and the DAOs themselves are handed a [FakeDatabase] that throws on
// ANY access — so a future edit that accidentally reintroduces a real DB call
// fails loudly instead of hanging.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/budgets_dao.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/models/comment.dart';
import 'package:lazyclaw_mobile/models/expense.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/providers/budgets_provider.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart';
import 'package:lazyclaw_mobile/repositories/budgets_repository.dart';
import 'package:lazyclaw_mobile/repositories/tasks_repository.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_detail_sheet.dart';
import 'package:lazyclaw_mobile/sync/budgets_sync.dart';
import 'package:lazyclaw_mobile/sync/task_sync.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
// `sqflite_sqlcipher` (a real dependency) rather than `sqflite_common`, which
// task_detail_sheet_test.dart reaches for transitively and gets linted for.
import 'package:sqflite_sqlcipher/sqflite.dart';

/// A Database that throws on any access — see the FakeAsync note above.
class FakeDatabase implements Database {
  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('Fake DB must not be touched in this test');
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

class _NoopTaskSync extends TaskSync {
  _NoopTaskSync(super.dao, super.repo);
  @override
  Future<SyncResult> sync({bool retryRejected = false}) async =>
      const SyncResult();
}

class _NoopBudgetsSync extends BudgetsSync {
  _NoopBudgetsSync(super.dao, super.repo);
  @override
  Future<BudgetsSyncResult> sync({bool retryRejected = false}) async =>
      const BudgetsSyncResult();
}

/// Records the sheet's writes and keeps a live [TasksState] so the sheet's
/// `ref.watch(tasksProvider)` sees comment adds/deletes without a round-trip.
class StubTasksNotifier extends TasksNotifier {
  StubTasksNotifier(super.dao, super.sync, Task seed) {
    state = TasksState(tasks: [seed]);
  }

  final List<Map<String, dynamic>> updateCalls = [];
  final List<String> deleteCalls = [];
  final List<Map<String, String?>> commentAdds = [];
  final List<String> commentDeletes = [];
  int _seq = 0;

  @override
  Future<void> updateTask(
    String id, {
    String? title,
    String? description,
    String? priority,
    String? dueDate,
    String? category,
    String? steps,
    String? reminderAt,
    String? recurring,
    String? recurUntil,
    String? tags,
    double? allocatedBudget,
    bool clearAllocatedBudget = false,
  }) async {
    updateCalls.add({
      'id': id,
      'title': title,
      'description': description,
      'category': category,
      'steps': steps,
      'tags': tags,
      'allocatedBudget': allocatedBudget,
      'clearAllocatedBudget': clearAllocatedBudget,
    });
  }

  @override
  Future<void> deleteTask(String id) async => deleteCalls.add(id);

  @override
  Future<TaskComment?> addComment(
    String taskId,
    String text, {
    String? subtaskId,
  }) async {
    final trimmed = text.trim();
    if (trimmed.isEmpty) return null;
    final created = TaskComment(
      id: 'persisted-${++_seq}',
      ts: '2026-08-03T09:0$_seq:00Z',
      author: 'user',
      text: trimmed,
      subtaskId: subtaskId,
    );
    commentAdds.add({
      'taskId': taskId,
      'text': trimmed,
      'subtaskId': subtaskId,
    });
    _rewriteComments(taskId, (existing) => [...existing, created]);
    return created;
  }

  @override
  Future<void> deleteComment(String taskId, String commentId) async {
    commentDeletes.add(commentId);
    _rewriteComments(
      taskId,
      (existing) => [
        for (final c in existing)
          if (c.id != commentId) c,
      ],
    );
  }

  /// Immutable state swap — a new comment list, a new [Task], a new list of
  /// tasks. Nothing already handed to a widget is mutated in place.
  void _rewriteComments(
    String taskId,
    List<TaskComment> Function(List<TaskComment>) transform,
  ) {
    state = state.copyWith(
      tasks: [
        for (final t in state.tasks)
          if (t.id == taskId)
            t.copyWith(comments: serializeComments(transform(t.taskComments)))
          else
            t,
      ],
    );
  }
}

/// Serves projects/expenses to the sheet and records `addExpense` calls,
/// appending an optimistic [Expense] so the spend rollup visibly refreshes
/// (exactly like the real notifier does).
class StubBudgetsNotifier extends BudgetsNotifier {
  StubBudgetsNotifier(
    super.dao,
    super.sync, {
    List<Project> projects = const [],
    List<Expense> expenses = const [],
  }) {
    state = BudgetsState(projects: projects, expenses: expenses);
  }

  final List<Map<String, Object?>> addCalls = [];

  /// Flip to false to exercise the failed-write path.
  bool addSucceeds = true;
  int _seq = 0;

  @override
  Future<void> load() async {}
  @override
  Future<void> refresh() async {}
  @override
  Future<void> syncNow() async {}

  @override
  Future<bool> addExpense(
    String projectId,
    double amount,
    String description, {
    String? vendor,
    String? taskId,
    String? subtaskId,
  }) async {
    addCalls.add({
      'projectId': projectId,
      'amount': amount,
      'description': description,
      'vendor': vendor,
      'taskId': taskId,
      'subtaskId': subtaskId,
    });
    if (!addSucceeds) return false;
    final currency = state.projects
        .where((p) => p.id == projectId)
        .firstOrNull
        ?.currency;
    state = state.copyWith(
      expenses: [
        ...state.expenses,
        Expense(
          id: 'local-${++_seq}',
          projectId: projectId,
          taskId: taskId,
          subtaskId: subtaskId,
          amount: amount,
          currency: currency ?? 'USD',
          description: description,
          status: 'posted',
        ),
      ],
    );
    return true;
  }
}

StubTasksNotifier makeTasksStub(Task seed) {
  final dao = TaskDao(FakeDatabase());
  return StubTasksNotifier(
    dao,
    _NoopTaskSync(dao, TasksRepository(_OfflineTasksTransport())),
    seed,
  );
}

StubBudgetsNotifier makeBudgetsStub({
  List<Project> projects = const [],
  List<Expense> expenses = const [],
}) {
  final dao = BudgetsDao(FakeDatabase());
  return StubBudgetsNotifier(
    dao,
    _NoopBudgetsSync(dao, BudgetsRepository(_OfflineBudgetsTransport())),
    projects: projects,
    expenses: expenses,
  );
}

Project makeProject(String id, String name, {String currency = 'USD'}) =>
    Project(
      id: id,
      name: name,
      budget: 0,
      currency: currency,
      status: 'active',
    );

Expense makeExpense({
  required String id,
  String projectId = 'p1',
  String? taskId,
  String? subtaskId,
  double amount = 10,
  String currency = 'USD',
  String status = 'posted',
}) => Expense(
  id: id,
  projectId: projectId,
  taskId: taskId,
  subtaskId: subtaskId,
  amount: amount,
  currency: currency,
  description: 'seed',
  status: status,
);

/// A `MaterialApp` whose only button opens the detail sheet for [task].
Widget detailSheetHost({
  required StubTasksNotifier tasks,
  required StubBudgetsNotifier budgets,
  required Task task,
  List<Project> pickerProjects = const [],
}) => ProviderScope(
  overrides: [
    tasksProvider.overrideWith((ref) => tasks),
    budgetsProvider.overrideWith((ref) => budgets),
  ],
  child: MaterialApp(
    theme: buildAppTheme(),
    home: Consumer(
      builder: (ctx, ref, _) => Scaffold(
        body: Center(
          child: ElevatedButton(
            onPressed: () =>
                showTaskDetailSheet(ctx, ref, task, projects: pickerProjects),
            child: const Text('open'),
          ),
        ),
      ),
    ),
  ),
);
