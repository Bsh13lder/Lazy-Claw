// D2 — tapping a sub-task's money sign inside the task detail sheet opens the
// Add Expense sheet PINNED to that sub-task, and the chip's total refreshes
// without the detail sheet closing.
//
// The SubtaskEditor-level contract (which rows get an affordance at all) is
// covered in subtask_editor_test.dart; this file covers the wiring.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_budget_control.dart';

import 'task_detail_harness.dart';

const _task = Task(
  id: 'task-9',
  userId: 'u1',
  title: 'Bake a cake',
  category: 'Home',
  priority: 'medium',
  status: 'todo',
  owner: 'user',
  nagCount: 0,
  createdAt: '2026-06-06T00:00:00Z',
  steps:
      '[{"id":"s1","title":"Buy flour","done":false},'
      '{"id":"s2","title":"Preheat oven","done":true}]',
);

/// Same task with no project — the "nowhere to file it" path.
const _taskNoProject = Task(
  id: 'task-9',
  userId: 'u1',
  title: 'Bake a cake',
  priority: 'medium',
  status: 'todo',
  owner: 'user',
  nagCount: 0,
  createdAt: '2026-06-06T00:00:00Z',
  steps: '[{"id":"s1","title":"Buy flour","done":false}]',
);

void main() {
  Future<StubBudgetsNotifier> open(
    WidgetTester tester, {
    Task task = _task,
    List<String> projectNames = const ['Home'],
  }) async {
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(900, 2400);
    addTearDown(tester.view.reset);

    final projects = [
      for (var i = 0; i < projectNames.length; i++)
        makeProject('p${i + 1}', projectNames[i]),
    ];
    final budgets = makeBudgetsStub(projects: projects);
    await tester.pumpWidget(
      detailSheetHost(
        tasks: makeTasksStub(task),
        budgets: budgets,
        task: task,
        pickerProjects: projects,
      ),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    return budgets;
  }

  testWidgets('every saved sub-task shows a money sign, unsaved ones do not', (
    tester,
  ) async {
    await open(tester);

    expect(find.byKey(const ValueKey('subtask-expense-s1')), findsOneWidget);
    expect(find.byKey(const ValueKey('subtask-expense-s2')), findsOneWidget);

    await tester.enterText(
      find.byKey(const Key('subtask-add-field')),
      'Add frosting',
    );
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pumpAndSettle();

    // The new row renders, but with no money sign — its id doesn't exist
    // server-side yet, so an expense could never point at it. Matched by
    // KEY, not by icon: the BUDGET dropdown deliberately wears the same
    // money glyph, so an icon count would silently include it.
    expect(find.text('Add frosting'), findsOneWidget);
    expect(
      find.byWidgetPredicate(
        (w) =>
            w.key is ValueKey<String> &&
            (w.key as ValueKey<String>).value.startsWith('subtask-expense-'),
      ),
      findsNWidgets(2),
    );
  });

  testWidgets(
    'tapping a sub-task money sign files the expense against THAT sub-task '
    'and refreshes its chip in place',
    (tester) async {
      final budgets = await open(tester);

      await tester.tap(find.byKey(const ValueKey('subtask-expense-s2')));
      await tester.pumpAndSettle();

      // Scoped mode names the sub-task so it can't be mistaken for a plain
      // project expense.
      expect(find.byKey(const Key('expense-context-label')), findsOneWidget);
      expect(find.text('Sub-task: Preheat oven'), findsOneWidget);

      await tester.enterText(
        find.byKey(const Key('expense-description-field')),
        'gas',
      );
      await tester.enterText(
        find.byKey(const Key('expense-amount-field')),
        '8',
      );
      await tester.tap(find.byKey(const Key('expense-submit-fab')));
      await tester.pumpAndSettle();

      expect(budgets.addCalls, hasLength(1));
      expect(budgets.addCalls.single['taskId'], 'task-9');
      expect(budgets.addCalls.single['subtaskId'], 's2');
      expect(budgets.addCalls.single['projectId'], 'p1');

      // Detail sheet still open, with the chip's total now rendered.
      expect(find.byKey(const Key('task-detail-title')), findsOneWidget);
      expect(find.text('\$8'), findsOneWidget);
      // Sub-task money also rolls into the task-level "spent" readout.
      expect(find.text('Spent \$8'), findsOneWidget);
    },
  );

  testWidgets(
    'with no project the sign is still shown but explains itself instead of '
    'opening a sheet that could never submit',
    (tester) async {
      final budgets = await open(
        tester,
        task: _taskNoProject,
        projectNames: const [],
      );

      await tester.tap(find.byKey(const ValueKey('subtask-expense-s1')));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('expense-description-field')), findsNothing);
      expect(find.text(kTaskBudgetNoProjectReason), findsOneWidget);
      expect(budgets.addCalls, isEmpty);
    },
  );
}
