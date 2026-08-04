// D1 — the task detail sheet's BUDGET section.
//
// Since the 2026-08-03 pass the ALLOCATED FIGURE ITSELF is the control: the
// readout is tappable and opens the allocation editor in place (there is no
// longer a separate "Allocated budget" dropdown item revealing a field beside
// it). The dropdown now carries the two ACTIONS — Top up (adds to the
// allocation) and Add expense (records real spend).
//
// Uses the shared harness (see task_detail_harness.dart) — no real DB, no
// network, FakeAsync-safe.
//
// Landmine: `Icons.attach_money_rounded` and `Icons.chat_bubble_outline` are
// each used by two different controls in this sheet, so everything here matches
// by Key, never by icon.
//
// The PURE helpers (labels, validation, top-up math) are asserted directly in
// task_budget_math_test.dart; this file only proves the wiring.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_budget_control.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

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

/// A project-owning task with an arbitrary allocation (null = none).
Task _budgeted(double? allocated) => Task(
  id: 'task-1',
  userId: 'u1',
  title: 'Kitchen reno',
  category: 'Home',
  priority: 'medium',
  status: 'todo',
  owner: 'user',
  nagCount: 0,
  createdAt: '2026-06-06T00:00:00Z',
  allocatedBudget: allocated,
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

  /// Open the sheet for a task that HAS a project (so Add expense is live) and
  /// the given allocation.
  Future<StubTasksNotifier> openBudgeted(
    WidgetTester tester,
    double? allocated, {
    List<double> spends = const [],
  }) async {
    final task = _budgeted(allocated);
    final tasks = makeTasksStub(task);
    await open(
      tester,
      tasks: tasks,
      budgets: makeBudgetsStub(
        projects: [makeProject('p1', 'Home')],
        expenses: [
          for (var i = 0; i < spends.length; i++)
            makeExpense(id: 'e$i', taskId: 'task-1', amount: spends[i]),
        ],
      ),
      task: task,
    );
    return tasks;
  }

  Future<void> openMenu(WidgetTester tester) async {
    await tester.ensureVisible(find.byKey(kTaskBudgetMenuKey));
    await tester.tap(find.byKey(kTaskBudgetMenuKey));
    await tester.pumpAndSettle();
  }

  /// Tap the allocated-vs-spent readout — the in-place edit affordance.
  Future<void> tapReadout(WidgetTester tester) async {
    await tester.ensureVisible(find.byKey(kTaskBudgetSummaryTapKey));
    await tester.tap(find.byKey(kTaskBudgetSummaryTapKey));
    await tester.pumpAndSettle();
  }

  Future<void> openTopUp(WidgetTester tester) async {
    await openMenu(tester);
    await tester.tap(find.byKey(kTaskBudgetTopUpItemKey));
    await tester.pumpAndSettle();
  }

  Future<void> save(WidgetTester tester) async {
    await tester.ensureVisible(find.byKey(const Key('task-detail-save')));
    await tester.tap(find.byKey(const Key('task-detail-save')));
    await tester.pumpAndSettle();
  }

  String allocatedFieldText(WidgetTester tester) => tester
      .widget<TextField>(find.byKey(const Key('task-detail-budget')))
      .controller!
      .text;

  // ── The dropdown (actions only, now that the figure edits itself) ──────────

  testWidgets('the money dropdown offers Top up and Add expense', (
    tester,
  ) async {
    await open(
      tester,
      tasks: makeTasksStub(_noProject),
      budgets: makeBudgetsStub(),
    );
    await openMenu(tester);

    expect(find.byKey(kTaskBudgetTopUpItemKey), findsOneWidget);
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

  // ── Edit in place ─────────────────────────────────────────────────────────

  testWidgets(
    'the allocation editor is closed until the readout is tapped, then it '
    'opens SEEDED with the current allocation',
    (tester) async {
      await openBudgeted(tester, 250);
      expect(find.byKey(const Key('task-detail-budget')), findsNothing);

      await tapReadout(tester);

      expect(find.byKey(const Key('task-detail-budget')), findsOneWidget);
      expect(allocatedFieldText(tester), '250');
    },
  );

  testWidgets(
    'a task with no allocation invites one and opens an EMPTY editor',
    (tester) async {
      await openBudgeted(tester, null);
      expect(find.text('No budget yet'), findsOneWidget);

      await tapReadout(tester);

      expect(allocatedFieldText(tester), isEmpty);
    },
  );

  testWidgets('editing the figure to a new number saves that number', (
    tester,
  ) async {
    final tasks = await openBudgeted(tester, 250);
    await tapReadout(tester);
    await tester.enterText(find.byKey(const Key('task-detail-budget')), '400');
    await tester.pumpAndSettle();

    // The readout tracks the edit before it is even saved.
    expect(find.text('Allocated \$400 · Spent \$0'), findsOneWidget);

    await save(tester);

    expect(tasks.updateCalls.single['allocatedBudget'], 400.0);
    expect(tasks.updateCalls.single['clearAllocatedBudget'], false);
  });

  testWidgets(
    'emptying the figure sends the CLEAR sentinel, not 0 — the three-way '
    'untouched/clear/set contract must survive the in-place editor',
    (tester) async {
      final tasks = await openBudgeted(tester, 250);
      await tapReadout(tester);
      await tester.enterText(find.byKey(const Key('task-detail-budget')), '');
      await tester.pumpAndSettle();

      await save(tester);

      expect(tasks.updateCalls.single['allocatedBudget'], isNull);
      expect(tasks.updateCalls.single['clearAllocatedBudget'], isTrue);
    },
  );

  testWidgets('an untouched budget is not written at all', (tester) async {
    final tasks = await openBudgeted(tester, 250);
    await save(tester);

    // Stronger than it used to be: the sheet auto-saves, so "no budget
    // change" now means no updateTask AT ALL rather than one carrying a null
    // budget. An open-and-close that writes would churn `updated_at` and,
    // under last-write-wins sync, could clobber a real edit made elsewhere.
    expect(tasks.updateCalls, isEmpty);
  });

  testWidgets(
    'a junk allocation is refused inline and blocks the save rather than '
    'being silently dropped',
    (tester) async {
      final tasks = await openBudgeted(tester, 250);
      await tapReadout(tester);
      await tester.enterText(
        find.byKey(const Key('task-detail-budget')),
        '-99',
      );
      await tester.pumpAndSettle();

      expect(find.text(kTaskAllocationNegativeError), findsOneWidget);

      await save(tester);
      expect(tasks.updateCalls, isEmpty);
      // The sheet stayed open so the user can fix it.
      expect(find.byKey(const Key('task-detail-title')), findsOneWidget);
    },
  );

  // ── Top up ────────────────────────────────────────────────────────────────

  testWidgets(
    'a top-up PREVIEWS the new total before committing, then adds it to the '
    'allocation',
    (tester) async {
      final tasks = await openBudgeted(tester, 300);
      await openTopUp(tester);

      await tester.enterText(find.byKey(kTaskTopUpFieldKey), '50');
      await tester.pumpAndSettle();

      // Preview only — nothing has changed yet.
      expect(find.text('New total \$350 (was \$300)'), findsOneWidget);
      expect(find.text('Allocated \$300 · Spent \$0'), findsOneWidget);

      await tester.ensureVisible(find.byKey(kTaskTopUpSubmitKey));
      await tester.tap(find.byKey(kTaskTopUpSubmitKey));
      await tester.pumpAndSettle();

      // Committed: the readout moved and the editor closed.
      expect(find.text('Allocated \$350 · Spent \$0'), findsOneWidget);
      expect(find.byKey(kTaskTopUpFieldKey), findsNothing);

      await save(tester);
      expect(tasks.updateCalls.single['allocatedBudget'], 350.0);
      expect(tasks.updateCalls.single['clearAllocatedBudget'], false);
    },
  );

  testWidgets(
    'a top-up on a task with NO allocation DEFINES one rather than failing',
    (tester) async {
      final tasks = await openBudgeted(tester, null);
      await openTopUp(tester);

      await tester.enterText(find.byKey(kTaskTopUpFieldKey), '50');
      await tester.pumpAndSettle();
      expect(find.text('New total \$50'), findsOneWidget);

      await tester.ensureVisible(find.byKey(kTaskTopUpSubmitKey));
      await tester.tap(find.byKey(kTaskTopUpSubmitKey));
      await tester.pumpAndSettle();

      expect(find.text('Allocated \$50 · Spent \$0'), findsOneWidget);
      await save(tester);
      expect(tasks.updateCalls.single['allocatedBudget'], 50.0);
    },
  );

  testWidgets(
    'an empty / non-numeric / negative top-up is refused inline and writes '
    'NOTHING',
    (tester) async {
      final tasks = await openBudgeted(tester, 300);
      await openTopUp(tester);
      await tester.ensureVisible(find.byKey(kTaskTopUpSubmitKey));

      // Empty submit.
      await tester.tap(find.byKey(kTaskTopUpSubmitKey));
      await tester.pumpAndSettle();
      expect(find.text(kTaskTopUpEmptyError), findsOneWidget);

      // Non-numeric.
      await tester.enterText(find.byKey(kTaskTopUpFieldKey), 'abc');
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(kTaskTopUpSubmitKey));
      await tester.pumpAndSettle();
      expect(find.text(kTaskTopUpInvalidError), findsOneWidget);

      // Negative.
      await tester.enterText(find.byKey(kTaskTopUpFieldKey), '-50');
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(kTaskTopUpSubmitKey));
      await tester.pumpAndSettle();
      expect(find.text(kTaskTopUpNonPositiveError), findsOneWidget);

      // The allocation never moved, so nothing is written at all — every
      // refusal above was inline, and a refused top-up must not reach the DB.
      expect(find.text('Allocated \$300 · Spent \$0'), findsOneWidget);
      await save(tester);
      expect(tasks.updateCalls, isEmpty);
    },
  );

  testWidgets('cancelling a top-up leaves the allocation alone', (
    tester,
  ) async {
    await openBudgeted(tester, 300);
    await openTopUp(tester);
    await tester.enterText(find.byKey(kTaskTopUpFieldKey), '50');
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.byKey(kTaskTopUpCancelKey));
    await tester.tap(find.byKey(kTaskTopUpCancelKey));
    await tester.pumpAndSettle();

    expect(find.byKey(kTaskTopUpFieldKey), findsNothing);
    expect(find.text('Allocated \$300 · Spent \$0'), findsOneWidget);
  });

  // ── The readout ───────────────────────────────────────────────────────────

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

  testWidgets('overspending the allocation still turns the readout red', (
    tester,
  ) async {
    await openBudgeted(tester, 250, spends: [300]);

    final label = tester.widget<Text>(find.byKey(kTaskBudgetSummaryKey));
    expect(label.style?.color, AppColors.error);
    expect(label.style?.fontWeight, FontWeight.w700);
  });

  testWidgets('staying under the allocation keeps the readout muted', (
    tester,
  ) async {
    await openBudgeted(tester, 250, spends: [30]);

    final label = tester.widget<Text>(find.byKey(kTaskBudgetSummaryKey));
    expect(label.style?.color, AppColors.textSecondary);
  });

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
}
