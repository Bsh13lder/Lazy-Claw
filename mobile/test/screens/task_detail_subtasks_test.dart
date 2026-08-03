// Widget tests for the sub-task editor inside the task detail sheet:
//   * existing sub-tasks render with a done/total progress label,
//   * add / toggle / delete / inline-edit all flow into the Save payload,
//   * a title-only edit does NOT write `steps` (no churn).
//
// Mirrors task_detail_sheet_test.dart: a stub TasksNotifier records the
// updateTask call (incl. the serialized `steps`) without touching the DAO. The
// surface is enlarged so the whole sheet fits without scrolling.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/budgets_dao.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/models/expense.dart';
import 'package:lazyclaw_mobile/models/subtask.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/providers/budgets_provider.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart';
import 'package:lazyclaw_mobile/repositories/budgets_repository.dart';
import 'package:lazyclaw_mobile/repositories/tasks_repository.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_detail_sheet.dart';
import 'package:lazyclaw_mobile/sync/budgets_sync.dart';
import 'package:lazyclaw_mobile/sync/task_sync.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:sqflite_sqlcipher/sqflite.dart';

class _OfflineTransport implements TasksTransport {
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

class _NoopSync extends TaskSync {
  _NoopSync(super.dao, super.repo);
  @override
  Future<SyncResult> sync({bool retryRejected = false}) async =>
      const SyncResult();
}

class _StubTasksNotifier extends TasksNotifier {
  _StubTasksNotifier(super.dao, super.sync);

  final List<Map<String, dynamic>> updateCalls = [];

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
    updateCalls.add({'id': id, 'title': title, 'steps': steps});
  }

  @override
  Future<void> deleteTask(String id) async {}
}

class _FakeDatabase implements Database {
  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('Fake DB must not be touched in this test');
}

_StubTasksNotifier _stub() {
  final dao = TaskDao(_FakeDatabase());
  return _StubTasksNotifier(
    dao,
    _NoopSync(dao, TasksRepository(_OfflineTransport())),
  );
}

// ── budgetsProvider stub ─────────────────────────────────────────────────────
//
// TaskDetailSheet now reads budgetsProvider to compute the sub-task money
// chip's per-sub-task expense totals. The real provider throws unless
// appDatabaseProvider is overridden with a live DB, so every test needs a
// stub — [_stubBudgets] optionally seeds [Expense]s for the money-chip
// integration coverage below.

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

class _NoopBudgetsSync extends BudgetsSync {
  _NoopBudgetsSync(super.dao, super.repo);
  @override
  Future<BudgetsSyncResult> sync({bool retryRejected = false}) async =>
      const BudgetsSyncResult();
}

class _StubBudgetsNotifier extends BudgetsNotifier {
  _StubBudgetsNotifier(super.dao, super.sync, List<Expense> expenses) {
    state = BudgetsState(expenses: expenses);
  }
  @override
  Future<void> load() async {}
  @override
  Future<void> refresh() async {}
  @override
  Future<void> syncNow() async {}
}

_StubBudgetsNotifier _stubBudgets([List<Expense> expenses = const []]) {
  final dao = BudgetsDao(_FakeDatabase());
  return _StubBudgetsNotifier(
    dao,
    _NoopBudgetsSync(dao, BudgetsRepository(_OfflineBudgetsTransport())),
    expenses,
  );
}

const _withSteps = Task(
  id: 'task-9',
  userId: 'u1',
  title: 'Bake a cake',
  priority: 'medium',
  status: 'todo',
  owner: 'user',
  nagCount: 0,
  createdAt: '2026-06-06T00:00:00Z',
  steps:
      '[{"id":"s1","title":"Buy flour","done":false},'
      '{"id":"s2","title":"Preheat oven","done":true}]',
);

void main() {
  Widget host(
    _StubTasksNotifier stub,
    Task task, {
    List<Expense> expenses = const [],
  }) => ProviderScope(
    overrides: [
      tasksProvider.overrideWith((ref) => stub),
      budgetsProvider.overrideWith((ref) => _stubBudgets(expenses)),
    ],
    child: MaterialApp(
      theme: buildAppTheme(),
      home: Consumer(
        builder: (ctx, ref, _) => Scaffold(
          body: Center(
            child: ElevatedButton(
              onPressed: () => showTaskDetailSheet(ctx, ref, task),
              child: const Text('open'),
            ),
          ),
        ),
      ),
    ),
  );

  Future<void> openSheet(
    WidgetTester tester,
    _StubTasksNotifier stub, {
    Task task = _withSteps,
    List<Expense> expenses = const [],
  }) async {
    // Tall surface so the whole sheet (incl. subtasks + Save) fits unscrolled.
    await tester.binding.setSurfaceSize(const Size(600, 2000));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester.pumpWidget(host(stub, task, expenses: expenses));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
  }

  Future<void> save(WidgetTester tester) async {
    await tester.tap(find.byKey(const Key('task-detail-save')));
    await tester.pumpAndSettle();
  }

  String? lastSteps(_StubTasksNotifier stub) =>
      stub.updateCalls.single['steps'] as String?;

  /// Every per-sub-task 💬 badge, matched by its `subtask-comments-<id>` key
  /// rather than by icon — the sheet's own task-level comments badge uses the
  /// same glyph and would otherwise be counted as a sub-task's.
  Finder subtaskCommentBadges() => find.byWidgetPredicate(
    (w) =>
        w.key is ValueKey<String> &&
        (w.key as ValueKey<String>).value.startsWith('subtask-comments-'),
  );

  testWidgets('renders existing sub-tasks + a done/total progress label', (
    tester,
  ) async {
    final stub = _stub();
    await openSheet(tester, stub);

    expect(find.text('Buy flour'), findsOneWidget);
    expect(find.text('Preheat oven'), findsOneWidget);
    expect(
      find.byKey(const Key('task-detail-subtask-progress')),
      findsOneWidget,
    );
    expect(find.text('1/2'), findsOneWidget);
  });

  testWidgets('a title-only edit leaves steps untouched (null, no churn)', (
    tester,
  ) async {
    final stub = _stub();
    await openSheet(tester, stub);
    await save(tester);

    expect(stub.updateCalls, hasLength(1));
    expect(lastSteps(stub), isNull);
  });

  testWidgets('adding a sub-task persists it in the Save payload', (
    tester,
  ) async {
    final stub = _stub();
    await openSheet(tester, stub);

    await tester.enterText(
      find.byKey(const Key('subtask-add-field')),
      'Add frosting',
    );
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pumpAndSettle();

    // It shows up immediately in the editor.
    expect(find.text('Add frosting'), findsOneWidget);

    await save(tester);

    final subs = parseSubtasks(lastSteps(stub));
    expect(
      subs.map((s) => s.title),
      containsAll(['Buy flour', 'Preheat oven', 'Add frosting']),
    );
    expect(subs.firstWhere((s) => s.title == 'Add frosting').done, isFalse);
  });

  testWidgets('toggling a sub-task persists the new done state', (
    tester,
  ) async {
    final stub = _stub();
    await openSheet(tester, stub);

    await tester.tap(find.byKey(const ValueKey('subtask-toggle-s1')));
    await tester.pumpAndSettle();
    await save(tester);

    final subs = parseSubtasks(lastSteps(stub));
    expect(subs.firstWhere((s) => s.id == 's1').done, isTrue);
  });

  testWidgets('deleting a sub-task persists the removal', (tester) async {
    final stub = _stub();
    await openSheet(tester, stub);

    await tester.tap(find.byKey(const ValueKey('subtask-delete-s2')));
    await tester.pumpAndSettle();
    await save(tester);

    final subs = parseSubtasks(lastSteps(stub));
    expect(subs.map((s) => s.id), ['s1']);
  });

  testWidgets('inline-editing a sub-task title persists the new text', (
    tester,
  ) async {
    final stub = _stub();
    await openSheet(tester, stub);

    await tester.tap(find.byKey(const ValueKey('subtask-text-s1')));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const ValueKey('subtask-edit-s1')),
      'Buy bread flour',
    );
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pumpAndSettle();
    await save(tester);

    final subs = parseSubtasks(lastSteps(stub));
    expect(subs.firstWhere((s) => s.id == 's1').title, 'Buy bread flour');
  });

  testWidgets(
    'a new (unsaved) sub-task shows no comment badge while a saved one '
    'keeps it',
    (tester) async {
      final stub = _stub();
      await openSheet(tester, stub); // _withSteps: saved sub-tasks s1, s2.

      // Both pre-existing (SAVED) sub-tasks get a comment affordance — the
      // detail sheet always wires onOpenComments, and their ids are present
      // in the watched provider task's parsed steps.
      //
      // Counted by KEY, not by icon: the sheet's own task-level comments
      // badge (D4) wears the same 💬 glyph, so an icon count would include it.
      expect(subtaskCommentBadges(), findsNWidgets(2));
      expect(find.byKey(const ValueKey('subtask-comments-s1')), findsOneWidget);
      expect(find.byKey(const ValueKey('subtask-comments-s2')), findsOneWidget);

      await tester.enterText(
        find.byKey(const Key('subtask-add-field')),
        'Add frosting',
      );
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pumpAndSettle();

      // The freshly-added sub-task renders immediately (in-sheet, unsaved)
      // but must NOT get a comment badge — the count stays at 2, not 3, since
      // a comment on it before Save would replay against an unknown
      // subtask_id server-side.
      expect(find.text('Add frosting'), findsOneWidget);
      expect(subtaskCommentBadges(), findsNWidgets(2));
    },
  );

  testWidgets(
    'editing a sub-task allows multiple lines (maxLines: null) so a long '
    "title wraps instead of scrolling off-screen",
    (tester) async {
      final stub = _stub();
      await openSheet(tester, stub);

      await tester.tap(find.byKey(const ValueKey('subtask-text-s1')));
      await tester.pumpAndSettle();

      final field = tester.widget<TextField>(
        find.byKey(const ValueKey('subtask-edit-s1')),
      );
      expect(field.maxLines, isNull);
      // The existing commit-on-done behavior is untouched.
      expect(field.textInputAction, TextInputAction.done);
    },
  );

  testWidgets('removing the last sub-task writes an empty string (clears)', (
    tester,
  ) async {
    const oneStep = Task(
      id: 'task-10',
      userId: 'u1',
      title: 'Single',
      priority: 'medium',
      status: 'todo',
      owner: 'user',
      nagCount: 0,
      createdAt: '2026-06-06T00:00:00Z',
      steps: '[{"id":"only","title":"Only step","done":false}]',
    );
    final stub = _stub();
    await openSheet(tester, stub, task: oneStep);

    await tester.tap(find.byKey(const ValueKey('subtask-delete-only')));
    await tester.pumpAndSettle();
    await save(tester);

    // Empty string (not null) so the DAO patch includes + clears the column.
    expect(lastSteps(stub), '');
  });

  // ── Sub-task money chip (expense rollup) ─────────────────────────────────
  //
  // NOTE (D2, 2026-08-03): the money sign is now an ADD affordance rendered
  // on EVERY saved sub-task, not only on ones that already have expenses —
  // so these tests assert on the rendered AMOUNT rather than on the presence
  // of the key/icon. The BUDGET dropdown also wears `attach_money_rounded`,
  // so raw icon counts would double-count it; use `_amountsShown` instead.

  group('sub-task money chip', () {
    Expense expense({
      required String id,
      required String taskId,
      String? subtaskId,
      double amount = 10.0,
      String currency = 'USD',
      String status = 'posted',
    }) => Expense(
      id: id,
      projectId: 'p1',
      taskId: taskId,
      subtaskId: subtaskId,
      amount: amount,
      currency: currency,
      description: 'expense',
      status: status,
    );

    testWidgets('shows a total for a sub-task with a linked expense', (
      tester,
    ) async {
      final stub = _stub();
      await openSheet(
        tester,
        stub,
        expenses: [
          expense(id: 'e1', taskId: 'task-9', subtaskId: 's1', amount: 12.5),
        ],
      );

      expect(find.byKey(const ValueKey('subtask-expense-s1')), findsOneWidget);
      expect(find.text('\$12.50'), findsOneWidget);
      // s2 is saved so it keeps its (bare) add affordance, but shows no
      // amount — nothing has been spent on it.
      expect(find.byKey(const ValueKey('subtask-expense-s2')), findsOneWidget);
      expect(find.text('\$0'), findsNothing);
    });

    testWidgets('sums multiple expenses linked to the same sub-task', (
      tester,
    ) async {
      final stub = _stub();
      await openSheet(
        tester,
        stub,
        expenses: [
          expense(id: 'e1', taskId: 'task-9', subtaskId: 's1', amount: 10.0),
          expense(id: 'e2', taskId: 'task-9', subtaskId: 's1', amount: 5.0),
        ],
      );

      expect(find.text('\$15'), findsOneWidget);
    });

    testWidgets('excludes a void expense and one linked to a different task', (
      tester,
    ) async {
      final stub = _stub();
      await openSheet(
        tester,
        stub,
        expenses: [
          expense(
            id: 'e-void',
            taskId: 'task-9',
            subtaskId: 's1',
            amount: 99.0,
            status: 'void',
          ),
          expense(
            id: 'e-other-task',
            taskId: 'task-other',
            subtaskId: 's1',
            amount: 50.0,
          ),
        ],
      );

      // The affordance is still there (s1 is saved), but neither excluded
      // expense contributes an amount to it.
      expect(find.byKey(const ValueKey('subtask-expense-s1')), findsOneWidget);
      expect(find.text('\$99'), findsNothing);
      expect(find.text('\$50'), findsNothing);
    });

    testWidgets(
      'a task-level expense (no subtask_id) shows NO amount on either '
      'sub-task — it belongs to the task, not to a row',
      (tester) async {
        final stub = _stub();
        await openSheet(
          tester,
          stub,
          expenses: [expense(id: 'e1', taskId: 'task-9', amount: 40.0)],
        );

        // It DOES roll into the task-level readout...
        expect(find.text('Spent \$40'), findsOneWidget);
        // ...but never onto a sub-task row.
        expect(
          find.descendant(
            of: find.byKey(const ValueKey('subtask-expense-s1')),
            matching: find.text('\$40'),
          ),
          findsNothing,
        );
        expect(
          find.descendant(
            of: find.byKey(const ValueKey('subtask-expense-s2')),
            matching: find.text('\$40'),
          ),
          findsNothing,
        );
      },
    );

    testWidgets('no amount anywhere when the task has no linked expenses', (
      tester,
    ) async {
      final stub = _stub();
      await openSheet(tester, stub);

      expect(find.text('No budget yet'), findsOneWidget);
      expect(find.textContaining('\$'), findsNothing);
    });
  });
}
