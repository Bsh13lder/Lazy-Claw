// Tests for `showAddExpenseForTaskSheet` — the task/sub-task-scoped entry
// point into the Add Expense sheet (used by the task detail sheet).
//
// What matters here is the WIRING, not the form: the helper must forward
// `taskId`/`subtaskId` into `BudgetsNotifier.addExpense` (the link fields
// threaded through the DAO/repo/outbox), must LOCK the project to the task's
// own project, and must report back whether the create succeeded.
//
// No real database: `budgetsProvider` is overridden with a recording notifier
// built on a throwing fake Database (the pattern established in
// expense_detail_sheet_test.dart). `testWidgets` runs inside FakeAsync, so a
// real sqflite call here would never complete and the test would hang.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/budgets_dao.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/providers/budgets_provider.dart';
import 'package:lazyclaw_mobile/repositories/budgets_repository.dart';
import 'package:lazyclaw_mobile/screens/expenses/add_expense_for_task.dart';
import 'package:lazyclaw_mobile/screens/expenses/add_expense_sheet.dart';
import 'package:lazyclaw_mobile/sync/budgets_sync.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:sqflite_sqlcipher/sqflite.dart';

Project _project(String id, String name) => Project(
      id: id,
      name: name,
      budget: 0,
      currency: 'USD',
      status: 'active',
    );

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
  Future<BudgetsSyncResult> sync({bool retryRejected = false}) async =>
      const BudgetsSyncResult();
}

class _FakeDatabase implements Database {
  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('Fake DB must not be touched in this test');
}

/// One recorded `addExpense` invocation.
class _AddCall {
  const _AddCall({
    required this.projectId,
    required this.amount,
    required this.description,
    this.vendor,
    this.taskId,
    this.subtaskId,
  });

  final String projectId;
  final double amount;
  final String description;
  final String? vendor;
  final String? taskId;
  final String? subtaskId;
}

class _RecordingBudgetsNotifier extends BudgetsNotifier {
  _RecordingBudgetsNotifier(
    super.dao,
    super.sync, {
    List<Project> projects = const [],
    this.result = true,
  }) {
    state = BudgetsState(projects: projects);
  }

  /// What `addExpense` reports back — flip to false to exercise the failure
  /// path (the sheet must stay open and the helper must return false).
  final bool result;

  final List<_AddCall> calls = [];

  @override
  Future<bool> addExpense(
    String projectId,
    double amount,
    String description, {
    String? vendor,
    String? taskId,
    String? subtaskId,
  }) async {
    calls.add(_AddCall(
      projectId: projectId,
      amount: amount,
      description: description,
      vendor: vendor,
      taskId: taskId,
      subtaskId: subtaskId,
    ));
    return result;
  }
}

_RecordingBudgetsNotifier _recorder({
  List<Project> projects = const [],
  bool result = true,
}) {
  final dao = BudgetsDao(_FakeDatabase());
  return _RecordingBudgetsNotifier(
    dao,
    _NoopSync(dao, BudgetsRepository(_OfflineTransport())),
    projects: projects,
    result: result,
  );
}

void main() {
  Finder descriptionField() => find.byKey(const Key('expense-description-field'));
  Finder amountField() => find.byKey(const Key('expense-amount-field'));
  Finder submitFab() => find.byKey(const Key('expense-submit-fab'));

  /// Hosts a single "open" button that runs [open] with a live BuildContext +
  /// WidgetRef, so the helper is exercised exactly the way the task detail
  /// sheet will call it.
  Widget host({
    required _RecordingBudgetsNotifier notifier,
    required Future<void> Function(BuildContext, WidgetRef) open,
  }) {
    return ProviderScope(
      overrides: [budgetsProvider.overrideWith((ref) => notifier)],
      child: MaterialApp(
        theme: buildAppTheme(),
        home: Consumer(
          builder: (context, ref, _) => Scaffold(
            body: Center(
              child: TextButton(
                key: const Key('open'),
                onPressed: () => open(context, ref),
                child: const Text('open'),
              ),
            ),
          ),
        ),
      ),
    );
  }

  testWidgets(
    'forwards taskId + subtaskId into addExpense and reports success',
    (tester) async {
      final notifier = _recorder(projects: [_project('p1', 'clubbay')]);
      bool? returned;

      await tester.pumpWidget(host(
        notifier: notifier,
        open: (context, ref) async {
          returned = await showAddExpenseForTaskSheet(
            context,
            ref,
            projectId: 'p1',
            taskId: 't1',
            subtaskId: 's1',
            contextLabel: 'Sub-task: Buy paint',
          );
        },
      ));

      await tester.tap(find.byKey(const Key('open')));
      await tester.pumpAndSettle();

      expect(find.text('Sub-task: Buy paint'), findsOneWidget);

      await tester.enterText(descriptionField(), 'paint rollers');
      await tester.pump();
      await tester.enterText(amountField(), '12.50');
      await tester.pump();
      await tester.tap(submitFab());
      await tester.pumpAndSettle();

      expect(notifier.calls, hasLength(1));
      final call = notifier.calls.single;
      expect(call.projectId, 'p1');
      expect(call.amount, 12.50);
      expect(call.description, 'paint rollers');
      expect(call.taskId, 't1');
      expect(call.subtaskId, 's1');
      expect(returned, isTrue);
    },
  );

  testWidgets(
    'a task-level (no sub-task) scope forwards taskId with a null subtaskId',
    (tester) async {
      final notifier = _recorder(projects: [_project('p1', 'clubbay')]);

      await tester.pumpWidget(host(
        notifier: notifier,
        open: (context, ref) => showAddExpenseForTaskSheet(
          context,
          ref,
          projectId: 'p1',
          taskId: 't1',
        ),
      ));

      await tester.tap(find.byKey(const Key('open')));
      await tester.pumpAndSettle();

      await tester.enterText(descriptionField(), 'permit fee 40');
      await tester.pump();
      await tester.tap(submitFab());
      await tester.pumpAndSettle();

      expect(notifier.calls.single.taskId, 't1');
      expect(notifier.calls.single.subtaskId, isNull);
      expect(notifier.calls.single.amount, 40.0);
    },
  );

  testWidgets(
    'the project is LOCKED to the task\'s project — no dropdown to re-file it '
    'under a different project (which would silently break the rollup)',
    (tester) async {
      final notifier = _recorder(
        projects: [_project('p1', 'clubbay'), _project('p2', 'Marketing')],
      );

      await tester.pumpWidget(host(
        notifier: notifier,
        open: (context, ref) => showAddExpenseForTaskSheet(
          context,
          ref,
          projectId: 'p2',
          taskId: 't1',
        ),
      ));

      await tester.tap(find.byKey(const Key('open')));
      await tester.pumpAndSettle();

      expect(find.byType(DropdownButton<String>), findsNothing);
      expect(find.byKey(const Key('expense-project-locked')), findsOneWidget);
      expect(find.text('Marketing'), findsOneWidget);
    },
  );

  testWidgets(
    'a failed create keeps the sheet open and returns false',
    (tester) async {
      final notifier =
          _recorder(projects: [_project('p1', 'clubbay')], result: false);
      bool? returned;

      await tester.pumpWidget(host(
        notifier: notifier,
        open: (context, ref) async {
          returned = await showAddExpenseForTaskSheet(
            context,
            ref,
            projectId: 'p1',
            taskId: 't1',
          );
        },
      ));

      await tester.tap(find.byKey(const Key('open')));
      await tester.pumpAndSettle();

      await tester.enterText(descriptionField(), 'brushes 9');
      await tester.pump();
      await tester.tap(submitFab());
      await tester.pumpAndSettle();

      // Sheet still on screen, nothing reported as saved.
      expect(submitFab(), findsOneWidget);
      expect(returned, isNull);

      // Dismissing an unsaved sheet reports failure, not success.
      Navigator.of(tester.element(submitFab())).pop();
      await tester.pumpAndSettle();
      expect(returned, isFalse);
    },
  );

  testWidgets(
    'the unscoped Money-tab shape still works: submitting from a '
    'LzBottomSheet.show<void> route pops cleanly',
    (tester) async {
      // Regression guard — AddExpenseSheet pops `true` so the task-scoped
      // helper can await a result; that must not blow up on the existing
      // `show<void>` call site in expenses_screen.dart.
      final notifier = _recorder(projects: [_project('p1', 'clubbay')]);
      var submitted = false;

      await tester.pumpWidget(host(
        notifier: notifier,
        open: (context, ref) => LzBottomSheet.show<void>(
          context,
          title: 'Add Expense',
          builder: (_) => AddExpenseSheet(
            projects: [_project('p1', 'clubbay')],
            initialProjectId: 'p1',
            onSubmit: (_, _, _, _) async {
              submitted = true;
              return true;
            },
          ),
        ),
      ));

      await tester.tap(find.byKey(const Key('open')));
      await tester.pumpAndSettle();

      await tester.enterText(descriptionField(), 'coffee 3');
      await tester.pump();
      await tester.tap(submitFab());
      await tester.pumpAndSettle();

      expect(submitted, isTrue);
      expect(submitFab(), findsNothing, reason: 'sheet popped');
    },
  );

  testWidgets(
    'on a short screen the form scrolls but the floating submit stays on '
    'screen and tappable — the exact failure the old bottom button had',
    (tester) async {
      // Small viewport => the sheet is taller than the space it gets, which is
      // what used to push the full-width "Add Expense" button past the bottom
      // edge (clipped, so "there is no save sign").
      tester.view.physicalSize = const Size(400, 520);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      final notifier = _recorder(projects: [_project('p1', 'clubbay')]);

      await tester.pumpWidget(host(
        notifier: notifier,
        open: (context, ref) => showAddExpenseForTaskSheet(
          context,
          ref,
          projectId: 'p1',
          taskId: 't1',
          contextLabel: 'Sub-task: Buy paint',
        ),
      ));

      await tester.tap(find.byKey(const Key('open')));
      await tester.pumpAndSettle();

      // The form is scrollable (so the last field is still reachable)…
      expect(
        find.descendant(of: submitFab(), matching: find.byType(Icon)),
        findsOneWidget,
      );
      expect(find.byType(Scrollable), findsWidgets);

      // …and the button itself is fully inside the viewport.
      final fabRect = tester.getRect(submitFab());
      expect(fabRect.bottom, lessThanOrEqualTo(520));
      expect(fabRect.right, lessThanOrEqualTo(400));

      // Still tappable with the form scrolled to the top (nothing to scroll to).
      await tester.enterText(descriptionField(), 'ladder 30');
      await tester.pump();
      await tester.tap(submitFab());
      await tester.pumpAndSettle();

      expect(notifier.calls.single.amount, 30.0);
    },
  );
}
