// D1 — the task detail sheet's BUDGET section is a money dropdown with two
// actions (edit the allocated figure / add a real expense) plus an
// allocated-vs-spent readout.
//
// Uses the shared harness (see task_detail_harness.dart) — no real DB, no
// network, FakeAsync-safe.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_budget_control.dart';

import 'task_detail_harness.dart';

const _noProject = Task(
  id: 'task-1',
  userId: 'u1',
  title: 'Loose task',
  priority: 'medium',
  status: 'todo',
  owner: 'user',
  nagCount: 0,
  createdAt: '2026-06-06T00:00:00Z',
);

const _withProject = Task(
  id: 'task-1',
  userId: 'u1',
  title: 'Kitchen reno',
  category: 'Home',
  priority: 'medium',
  status: 'todo',
  owner: 'user',
  nagCount: 0,
  createdAt: '2026-06-06T00:00:00Z',
  allocatedBudget: 250,
);

void main() {
  Future<void> open(
    WidgetTester tester, {
    required StubTasksNotifier tasks,
    required StubBudgetsNotifier budgets,
    Task task = _noProject,
    bool withPickerProjects = true,
  }) async {
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(900, 2400);
    addTearDown(tester.view.reset);
    await tester.pumpWidget(
      detailSheetHost(
        tasks: tasks,
        budgets: budgets,
        task: task,
        pickerProjects: withPickerProjects ? budgets.state.projects : const [],
      ),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
  }

  Future<void> openMenu(WidgetTester tester) async {
    await tester.ensureVisible(find.byKey(kTaskBudgetMenuKey));
    await tester.tap(find.byKey(kTaskBudgetMenuKey));
    await tester.pumpAndSettle();
  }

  testWidgets('the money dropdown offers both actions', (tester) async {
    await open(
      tester,
      tasks: makeTasksStub(_noProject),
      budgets: makeBudgetsStub(),
    );
    await openMenu(tester);

    expect(find.byKey(kTaskBudgetAllocatedItemKey), findsOneWidget);
    expect(find.byKey(kTaskBudgetExpenseItemKey), findsOneWidget);
  });

  testWidgets(
    'with no project, Add expense is DISABLED and says why — the scoped '
    'sheet could never submit without a destination project',
    (tester) async {
      await open(
        tester,
        tasks: makeTasksStub(_noProject),
        budgets: makeBudgetsStub(),
      );
      await openMenu(tester);

      final item = tester.widget<PopupMenuItem<TaskBudgetAction>>(
        find.byKey(kTaskBudgetExpenseItemKey),
      );
      expect(item.enabled, isFalse);
      expect(find.text(kTaskBudgetNoProjectReason), findsOneWidget);
    },
  );

  testWidgets('with a project, Add expense is enabled', (tester) async {
    final budgets = makeBudgetsStub(projects: [makeProject('p1', 'Home')]);
    await open(
      tester,
      tasks: makeTasksStub(_withProject),
      budgets: budgets,
      task: _withProject,
    );
    await openMenu(tester);

    final item = tester.widget<PopupMenuItem<TaskBudgetAction>>(
      find.byKey(kTaskBudgetExpenseItemKey),
    );
    expect(item.enabled, isTrue);
    expect(find.text(kTaskBudgetNoProjectReason), findsNothing);
  });

  testWidgets(
    'the allocated field is hidden until the dropdown reveals it (a task '
    'with no budget yet)',
    (tester) async {
      await open(
        tester,
        tasks: makeTasksStub(_noProject),
        budgets: makeBudgetsStub(),
      );
      expect(find.byKey(const Key('task-detail-budget')), findsNothing);

      await openMenu(tester);
      await tester.tap(find.byKey(kTaskBudgetAllocatedItemKey));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('task-detail-budget')), findsOneWidget);
    },
  );

  testWidgets('a task that already has a budget shows the field up-front', (
    tester,
  ) async {
    await open(
      tester,
      tasks: makeTasksStub(_withProject),
      budgets: makeBudgetsStub(projects: [makeProject('p1', 'Home')]),
      task: _withProject,
    );

    final field = tester.widget<TextField>(
      find.byKey(const Key('task-detail-budget')),
    );
    expect(field.controller!.text, '250');
  });

  testWidgets(
    'the readout shows allocated AND spent, and spent folds in sub-task money',
    (tester) async {
      await open(
        tester,
        tasks: makeTasksStub(_withProject),
        budgets: makeBudgetsStub(
          projects: [makeProject('p1', 'Home')],
          expenses: [
            makeExpense(id: 'e1', taskId: 'task-1', amount: 30),
            makeExpense(
              id: 'e2',
              taskId: 'task-1',
              subtaskId: 's1',
              amount: 10,
            ),
            // Void + other-task rows must not count.
            makeExpense(
              id: 'e3',
              taskId: 'task-1',
              amount: 500,
              status: 'void',
            ),
            makeExpense(id: 'e4', taskId: 'task-other', amount: 900),
          ],
        ),
        task: _withProject,
      );

      expect(find.byKey(kTaskBudgetSummaryKey), findsOneWidget);
      expect(find.text('Allocated \$250 · Spent \$40'), findsOneWidget);
    },
  );

  testWidgets(
    'Add expense opens the project-locked sheet, links the created expense '
    'to the task, and the spent figure refreshes without closing the sheet',
    (tester) async {
      final budgets = makeBudgetsStub(projects: [makeProject('p1', 'Home')]);
      await open(
        tester,
        tasks: makeTasksStub(_withProject),
        budgets: budgets,
        task: _withProject,
      );

      await openMenu(tester);
      await tester.tap(find.byKey(kTaskBudgetExpenseItemKey));
      await tester.pumpAndSettle();

      // Scoped mode: the project is read-only and a context pill names the task.
      expect(find.byKey(const Key('expense-project-locked')), findsOneWidget);

      await tester.enterText(
        find.byKey(const Key('expense-description-field')),
        'tiles',
      );
      await tester.enterText(
        find.byKey(const Key('expense-amount-field')),
        '15',
      );
      await tester.tap(find.byKey(const Key('expense-submit-fab')));
      await tester.pumpAndSettle();

      expect(budgets.addCalls, hasLength(1));
      expect(budgets.addCalls.single['projectId'], 'p1');
      expect(budgets.addCalls.single['taskId'], 'task-1');
      // A TASK-level add carries no sub-task link.
      expect(budgets.addCalls.single['subtaskId'], isNull);

      // Back on the detail sheet (never closed), with a refreshed rollup.
      expect(find.byKey(const Key('task-detail-title')), findsOneWidget);
      expect(find.text('Allocated \$250 · Spent \$15'), findsOneWidget);
    },
  );

  group('taskBudgetSummaryLabel (pure)', () {
    test('nothing allocated and nothing spent reads as an invitation', () {
      expect(taskBudgetSummaryLabel(null, 0, 'USD'), 'No budget yet');
    });

    test('spend with no allocation shows only the spend', () {
      expect(taskBudgetSummaryLabel(null, 12.5, 'EUR'), 'Spent €12.50');
    });

    test('both present read side by side', () {
      expect(
        taskBudgetSummaryLabel(250, 40, 'USD'),
        'Allocated \$250 · Spent \$40',
      );
    });

    test('an allocation with nothing spent still shows a zero spend', () {
      expect(
        taskBudgetSummaryLabel(250, 0, 'USD'),
        'Allocated \$250 · Spent \$0',
      );
    });
  });

  group('taskBudgetOverspent (pure)', () {
    test('is false without an allocation, however much was spent', () {
      expect(taskBudgetOverspent(null, 900), isFalse);
    });

    test('is false at exactly the allocation', () {
      expect(taskBudgetOverspent(100, 100), isFalse);
    });

    test('is true past the allocation', () {
      expect(taskBudgetOverspent(100, 100.01), isTrue);
    });
  });
}
