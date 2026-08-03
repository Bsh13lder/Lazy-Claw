// Widget tests for the Add Expense sheet's smart-typing Description field:
//   * typing the user's literal example pre-fills the Amount field + resolves
//     the `#project` token to an existing project,
//   * a manual edit to the Amount field always wins over a later re-parse,
//   * a typed bare amount (no currency in the text) still calls onSubmit with
//     the same 4-arg shape the manual-entry path always used — pinning that
//     quick-typing introduces no NEW currency divergence,
//   * an unmatched `#project` token shows the create-project suggestion row,
//     and tapping it creates + applies the project.
//
// AddExpenseSheet only reads `budgetsProvider` from the "Create project" path
// (everything else is plain callbacks), so most tests need nothing but a bare
// ProviderScope; only the create-flow test overrides it with a stub notifier
// backed by a throwing fake Database (never touched — see
// expense_detail_sheet_test.dart, which established this pattern).

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
  Widget host({
    required List<Project> projects,
    String? initialProjectId,
    required Future<bool> Function(String, double, String, String?) onSubmit,
    _StubBudgetsNotifier? budgetsStub,
  }) {
    final sheet = MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        body: SingleChildScrollView(
          child: AddExpenseSheet(
            projects: projects,
            initialProjectId: initialProjectId,
            onSubmit: onSubmit,
          ),
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

  String amountText(WidgetTester tester) =>
      tester.widget<TextField>(amountField()).controller!.text;

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

      await tester.ensureVisible(find.text('Add Expense'));
      await tester.pump();
      await tester.tap(find.text('Add Expense'));
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
}
