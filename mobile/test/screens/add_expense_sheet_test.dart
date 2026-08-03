// Widget tests for the Add Expense sheet.
//
// Field ORDER (2026-08-03): Description is first + autofocused, Amount second.
// The Description field is the smart one — typing `spent on #clubbay 25`
// live-parses the amount and the `#project` token and pre-fills everything
// below it. Opening on the Amount field raised a numeric keypad and fought
// that whole interaction, so the order was flipped. The tests below pin BOTH
// the new order AND that the smart-parse pre-fills survived the reorder (the
// actual regression risk).
//
// Submit affordance: the full-width "Add Expense" button used to sit at the
// END of the column, which meant a grown form (suggestion strip + keyboard)
// pushed it past the sheet's bottom edge — users read that as "there is no
// save button". It is now an [LzFloatingSubmit] pinned to the bottom-right of
// the sheet viewport, keyed `expense-submit-fab`.
//
// Also covered: manual-amount-wins-over-reparse, the `#project` suggestion
// strip (match / ambiguous / create), and task-scoped mode's LOCKED project
// field (see also test/screens/expenses/add_expense_for_task_test.dart).
//
// AddExpenseSheet only reads `budgetsProvider` from the "Create project" path
// (everything else is plain callbacks), so most tests need nothing but a bare
// ProviderScope; only the create-flow test overrides it with a stub notifier
// backed by a throwing fake Database (never touched — see
// expense_detail_sheet_test.dart, which established this pattern).

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/budgets_dao.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/providers/budgets_provider.dart';
import 'package:lazyclaw_mobile/repositories/budgets_repository.dart';
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

/// A Database that throws on any access — the stub notifier below overrides
/// every method that would touch it, so it is never actually invoked; it only
/// satisfies BudgetsDao's non-null constructor arg without spinning up a real
/// sqflite isolate.
class _FakeDatabase implements Database {
  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('Fake DB must not be touched in this test');
}

/// Records `createProject` calls and appends the new project to its own
/// state, mirroring what the real notifier does after a successful create —
/// `_createProjectFromSuggestion` reads `ref.read(budgetsProvider).projects`
/// right after, so the stub must actually grow the list for that to work.
class _StubBudgetsNotifier extends BudgetsNotifier {
  _StubBudgetsNotifier(super.dao, super.sync, {List<Project> projects = const []}) {
    state = BudgetsState(projects: projects);
  }

  final List<String> createCalls = [];

  @override
  Future<bool> createProject(
    String name, {
    double? budget,
    String? color,
    String? startDate,
    String? dueDate,
  }) async {
    createCalls.add(name);
    state = state.copyWith(projects: [
      ...state.projects,
      _project('new-${createCalls.length}', name),
    ]);
    return true;
  }
}

_StubBudgetsNotifier _stub({List<Project> projects = const []}) {
  final dao = BudgetsDao(_FakeDatabase());
  return _StubBudgetsNotifier(
    dao,
    _NoopSync(dao, BudgetsRepository(_OfflineTransport())),
    projects: projects,
  );
}

void main() {
  // NOTE: the sheet now owns its own SingleChildScrollView (via
  // LzFloatingSubmitLayout), so the host must give it BOUNDED height and must
  // NOT wrap it in another scroll view — nesting two vertical viewports gives
  // the inner one unbounded height and throws.
  Widget host({
    required List<Project> projects,
    String? initialProjectId,
    String? lockedProjectId,
    String? contextLabel,
    required Future<bool> Function(String, double, String, String?) onSubmit,
    _StubBudgetsNotifier? budgetsStub,
  }) {
    final sheet = MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        body: AddExpenseSheet(
          projects: projects,
          initialProjectId: initialProjectId,
          lockedProjectId: lockedProjectId,
          contextLabel: contextLabel,
          onSubmit: onSubmit,
        ),
      ),
    );
    if (budgetsStub == null) return ProviderScope(child: sheet);
    return ProviderScope(
      overrides: [budgetsProvider.overrideWith((ref) => budgetsStub)],
      child: sheet,
    );
  }

  Finder amountField() => find.byKey(const Key('expense-amount-field'));
  Finder descriptionField() => find.byKey(const Key('expense-description-field'));
  Finder submitFab() => find.byKey(const Key('expense-submit-fab'));

  String amountText(WidgetTester tester) =>
      tester.widget<TextField>(amountField()).controller!.text;

  // ── C1: field order ───────────────────────────────────────────────────────

  testWidgets(
    'Description is the FIRST field and holds focus on open; Amount is second',
    (tester) async {
      await tester.pumpWidget(host(
        projects: [_project('p1', 'clubbay')],
        onSubmit: (_, _, _, _) async => true,
      ));
      await tester.pump();

      final fields = find.byType(TextField);
      expect(
        tester.widget<TextField>(fields.at(0)).key,
        const Key('expense-description-field'),
      );
      expect(
        tester.widget<TextField>(fields.at(1)).key,
        const Key('expense-amount-field'),
      );

      // Autofocus lands on Description, not on the numeric Amount keypad.
      final desc = tester.widget<EditableText>(
        find.descendant(of: descriptionField(), matching: find.byType(EditableText)),
      );
      expect(desc.focusNode.hasFocus, isTrue);
      expect(tester.widget<TextField>(amountField()).autofocus, isFalse);
    },
  );

  testWidgets(
    'the keyboard Next chain matches the new order: Description → Amount → '
    'Vendor(done)',
    (tester) async {
      await tester.pumpWidget(host(
        projects: [_project('p1', 'clubbay')],
        onSubmit: (_, _, _, _) async => true,
      ));

      final fields = find.byType(TextField);
      expect(
        tester.widget<TextField>(fields.at(0)).textInputAction,
        TextInputAction.next,
      );
      expect(
        tester.widget<TextField>(fields.at(1)).textInputAction,
        TextInputAction.next,
      );
      // Vendor is the last field — it submits.
      expect(
        tester.widget<TextField>(fields.at(2)).textInputAction,
        TextInputAction.done,
      );
    },
  );

  testWidgets(
    'the example hint stays attached to the Description field',
    (tester) async {
      await tester.pumpWidget(host(
        projects: [_project('p1', 'clubbay')],
        onSubmit: (_, _, _, _) async => true,
      ));

      expect(find.text('25 · €45.50 · 40 eur · #project'), findsOneWidget);
    },
  );

  // ── Smart-typing (must survive the reorder) ───────────────────────────────

  testWidgets(
    'typing the literal example pre-fills the amount and resolves the project',
    (tester) async {
      await tester.pumpWidget(host(
        projects: [_project('p1', 'clubbay')],
        onSubmit: (_, _, _, _) async => true,
      ));

      await tester.enterText(descriptionField(), 'spent on #clubbay 25');
      await tester.pump();

      expect(amountText(tester), '25');

      final dropdown =
          tester.widget<DropdownButton<String>>(find.byType(DropdownButton<String>));
      expect(dropdown.value, 'p1');
    },
  );

  testWidgets(
    '"Clubbay VIP" + #clubbay auto-applies via a single substring hit — '
    'matches the agent-side resolver (lazyclaw/budgets/resolver.py), not '
    'just an exact-name match',
    (tester) async {
      await tester.pumpWidget(host(
        projects: [_project('p1', 'Clubbay VIP')],
        onSubmit: (_, _, _, _) async => true,
      ));

      await tester.enterText(descriptionField(), 'spent on #clubbay 25');
      await tester.pump();

      final dropdown = tester
          .widget<DropdownButton<String>>(find.byType(DropdownButton<String>));
      expect(dropdown.value, 'p1');
      // Auto-applied — no disambiguation strip shown.
      expect(
        find.byKey(const Key('expense-project-suggest-create')),
        findsNothing,
      );
    },
  );

  testWidgets(
    'two projects both containing "club" — ambiguous, shows the strip '
    'instead of silently guessing',
    (tester) async {
      // No `initialProjectId` and TWO candidate projects means the picker's
      // own default-first-project seed lands on 'p1' regardless of anything
      // the parser does — that default is unrelated to project RESOLUTION,
      // so the behavior actually under test is the strip appearing (proof
      // `resolveProjectMatch` returned null for the ambiguous "club" query,
      // rather than silently picking `p1` or `p2`).
      await tester.pumpWidget(host(
        projects: [_project('p1', 'Clubhouse'), _project('p2', 'Nightclub')],
        onSubmit: (_, _, _, _) async => true,
      ));

      await tester.enterText(descriptionField(), '25 #club drinks');
      await tester.pump();

      expect(
        find.byKey(const Key('expense-project-suggest-create')),
        findsOneWidget,
      );
    },
  );

  testWidgets(
    'no match at all — shows the strip (create-project offer)',
    (tester) async {
      await tester.pumpWidget(host(
        projects: [_project('p1', 'Marketing')],
        onSubmit: (_, _, _, _) async => true,
      ));

      await tester.enterText(descriptionField(), '25 #nima lunch');
      await tester.pump();

      expect(
        find.byKey(const Key('expense-project-suggest-create')),
        findsOneWidget,
      );
    },
  );

  testWidgets(
    'a manual amount edit wins over a later re-parse',
    (tester) async {
      await tester.pumpWidget(host(
        projects: [_project('p1', 'clubbay')],
        onSubmit: (_, _, _, _) async => true,
      ));

      await tester.enterText(descriptionField(), 'spent on #clubbay 25');
      await tester.pump();
      expect(amountText(tester), '25');

      // Manual override.
      await tester.enterText(amountField(), '99');
      await tester.pump();
      expect(amountText(tester), '99');

      // A further re-parse (different amount in the text) must NOT clobber
      // the manual edit.
      await tester.enterText(descriptionField(), 'spent on #clubbay 25 taxi 40');
      await tester.pump();
      expect(amountText(tester), '99');
    },
  );

  testWidgets(
    'a typed bare-amount expense calls onSubmit with the same 4-arg shape as '
    'manual entry — no currency is smuggled in, so quick-typing introduces no '
    'NEW divergence from the existing form-based add path',
    (tester) async {
      String? capturedProjectId;
      double? capturedAmount;
      String? capturedDescription;
      String? capturedVendor;

      await tester.pumpWidget(host(
        projects: [_project('p1', 'clubbay')],
        onSubmit: (projectId, amount, description, vendor) async {
          capturedProjectId = projectId;
          capturedAmount = amount;
          capturedDescription = description;
          capturedVendor = vendor;
          return true;
        },
      ));

      await tester.enterText(descriptionField(), 'spent on #clubbay 25');
      await tester.pump();

      await tester.tap(submitFab());
      await tester.pumpAndSettle();

      expect(capturedProjectId, 'p1');
      expect(capturedAmount, 25.0);
      expect(capturedDescription, 'spent on');
      expect(capturedVendor, isNull);
    },
  );

  testWidgets(
    'no strip when the description has no project token',
    (tester) async {
      await tester.pumpWidget(host(
        projects: [_project('p1', 'clubbay')],
        onSubmit: (_, _, _, _) async => true,
      ));

      await tester.enterText(descriptionField(), '12.90 coffee');
      await tester.pump();

      expect(
        find.byKey(const Key('expense-project-suggest-create')),
        findsNothing,
      );
    },
  );

  testWidgets(
    'an unmatched project token offers to create it; tapping create applies '
    'the newly-created project',
    (tester) async {
      final stub = _stub(projects: [_project('p1', 'clubbay')]);
      await tester.pumpWidget(host(
        projects: [_project('p1', 'clubbay')],
        onSubmit: (_, _, _, _) async => true,
        budgetsStub: stub,
      ));

      await tester.enterText(descriptionField(), '25 #nima lunch');
      await tester.pump();

      expect(
        find.byKey(const Key('expense-project-suggest-create')),
        findsOneWidget,
      );

      await tester.tap(find.byKey(const Key('expense-project-suggest-create')));
      await tester.pump();

      expect(stub.createCalls, ['nima']);
      final dropdown =
          tester.widget<DropdownButton<String>>(find.byType(DropdownButton<String>));
      expect(dropdown.value, 'new-1');

      // The strip closes once the token is resolved (touched).
      expect(
        find.byKey(const Key('expense-project-suggest-create')),
        findsNothing,
      );
    },
  );

  // ── C2: the floating square submit ────────────────────────────────────────

  testWidgets(
    'the submit affordance is the keyed floating square — the old full-width '
    '"Add Expense" button is GONE (not shipped alongside it)',
    (tester) async {
      await tester.pumpWidget(host(
        projects: [_project('p1', 'clubbay')],
        onSubmit: (_, _, _, _) async => true,
      ));

      expect(submitFab(), findsOneWidget);
      expect(find.widgetWithText(LzButton, 'Add Expense'), findsNothing);

      // It carries no visible label, so it MUST announce itself.
      expect(tester.widget<LzFloatingSubmit>(submitFab()).tooltip, 'Add expense');
    },
  );

  testWidgets(
    'the floating submit shows a spinner and is disabled while submitting',
    (tester) async {
      final gate = Completer<bool>();
      var calls = 0;

      await tester.pumpWidget(host(
        projects: [_project('p1', 'clubbay')],
        onSubmit: (_, _, _, _) {
          calls++;
          return gate.future;
        },
      ));

      await tester.enterText(descriptionField(), 'taxi 25');
      await tester.pump();

      await tester.tap(submitFab());
      await tester.pump();

      expect(calls, 1);
      final fab = tester.widget<LzFloatingSubmit>(submitFab());
      expect(fab.loading, isTrue);
      expect(fab.onPressed, isNull, reason: 're-tap must be impossible');
      expect(
        find.descendant(
          of: submitFab(),
          matching: find.byType(CircularProgressIndicator),
        ),
        findsOneWidget,
      );

      // Settle the in-flight submit so the spinner stops (pumpAndSettle would
      // otherwise spin forever on the indeterminate indicator).
      gate.complete(false);
      await tester.pumpAndSettle();
      expect(tester.widget<LzFloatingSubmit>(submitFab()).loading, isFalse);
    },
  );

  testWidgets(
    'submitting with an invalid amount surfaces the inline error and does not '
    'call onSubmit',
    (tester) async {
      var calls = 0;
      await tester.pumpWidget(host(
        projects: [_project('p1', 'clubbay')],
        onSubmit: (_, _, _, _) async {
          calls++;
          return true;
        },
      ));

      await tester.enterText(descriptionField(), 'coffee');
      await tester.pump();
      await tester.tap(submitFab());
      await tester.pump();

      expect(calls, 0);
      expect(find.text('Enter a valid amount'), findsOneWidget);
    },
  );

  // ── C3: task-scoped mode (locked project) ─────────────────────────────────

  testWidgets(
    'normal mode keeps the editable project dropdown',
    (tester) async {
      await tester.pumpWidget(host(
        projects: [_project('p1', 'clubbay')],
        onSubmit: (_, _, _, _) async => true,
      ));

      expect(find.byType(DropdownButton<String>), findsOneWidget);
      expect(find.byKey(const Key('expense-project-locked')), findsNothing);
      expect(find.byKey(const Key('expense-context-label')), findsNothing);
    },
  );

  testWidgets(
    'task-scoped mode locks the project (read-only, no dropdown) and shows the '
    'context label',
    (tester) async {
      await tester.pumpWidget(host(
        projects: [_project('p1', 'clubbay'), _project('p2', 'Marketing')],
        initialProjectId: 'p2',
        lockedProjectId: 'p1',
        contextLabel: 'Sub-task: Buy paint',
        onSubmit: (_, _, _, _) async => true,
      ));

      expect(find.byType(DropdownButton<String>), findsNothing);
      expect(find.byKey(const Key('expense-project-locked')), findsOneWidget);
      expect(find.text('clubbay'), findsOneWidget);
      expect(find.text('Sub-task: Buy paint'), findsOneWidget);
    },
  );

  testWidgets(
    'a #project token can NOT re-file a task-scoped expense — the lock wins '
    'and no suggestion strip is offered',
    (tester) async {
      String? capturedProjectId;
      await tester.pumpWidget(host(
        projects: [_project('p1', 'clubbay'), _project('p2', 'Marketing')],
        lockedProjectId: 'p1',
        onSubmit: (projectId, _, _, _) async {
          capturedProjectId = projectId;
          return true;
        },
      ));

      await tester.enterText(descriptionField(), 'paint #marketing 25');
      await tester.pump();

      expect(
        find.byKey(const Key('expense-project-suggest-create')),
        findsNothing,
      );

      await tester.tap(submitFab());
      await tester.pumpAndSettle();

      expect(capturedProjectId, 'p1');
    },
  );
}
