// Widget tests for the online-only Budget ledger sheet (add-to-budget top-ups
// + the credits/debits Log).
//
// The sheet reads budgetsRepositoryProvider for all CRUD, so we override it with
// a BudgetsRepository backed by a recording fake transport — no DB, no network.
// onBudgetChanged is a counter so we can assert the parent-refresh hook fires.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/providers/budgets_provider.dart';
import 'package:lazyclaw_mobile/repositories/budgets_repository.dart';
import 'package:lazyclaw_mobile/screens/expenses/budget_log_sheet.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

class _Call {
  final String method;
  final String path;
  final Map<String, dynamic>? body;
  _Call(this.method, this.path, this.body);
}

/// Records every transport call and answers method-aware shapes so both the
/// list GET and the mutation responses parse cleanly.
class _RecordingTransport implements BudgetsTransport {
  final List<_Call> calls = [];
  List<Map<String, dynamic>> entries;

  _RecordingTransport(this.entries);

  @override
  Future<Map<String, dynamic>> getJson(String path,
      {Map<String, dynamic>? queryParams}) async {
    calls.add(_Call('GET', path, null));
    return {'entries': entries, 'count': entries.length};
  }

  @override
  Future<Map<String, dynamic>> postJson(
      String path, Map<String, dynamic> body) async {
    calls.add(_Call('POST', path, body));
    return {
      'entry': {
        'id': 'new',
        'project_id': 'proj1',
        'amount': body['amount'],
        'currency': body['currency'] ?? 'USD',
        'source': body['source'],
        'kind': 'credit',
        'created_at': '2026-06-06T10:00:00Z',
      }
    };
  }

  @override
  Future<Map<String, dynamic>> patchJson(
      String path, Map<String, dynamic> body) async {
    calls.add(_Call('PATCH', path, body));
    return {
      'entry': {'id': 'edited', 'amount': body['amount'], 'kind': 'credit'}
    };
  }

  @override
  Future<Map<String, dynamic>> deleteJson(String path) async {
    calls.add(_Call('DELETE', path, null));
    return {'status': 'deleted'};
  }
}

Map<String, dynamic> _entry({
  String id = 'be1',
  double amount = 200.0,
  String? source = 'client deposit',
  String kind = 'credit',
}) =>
    {
      'id': id,
      'project_id': 'proj1',
      'amount': amount,
      'currency': 'USD',
      'source': source,
      'kind': kind,
      'created_at': '2026-06-05T10:00:00Z',
    };

void main() {
  Widget host(_RecordingTransport t, {Future<void> Function()? onChanged}) {
    return ProviderScope(
      overrides: [
        budgetsRepositoryProvider.overrideWithValue(BudgetsRepository(t)),
      ],
      child: MaterialApp(
        theme: buildAppTheme(),
        home: Scaffold(
          body: BudgetLogSheet(
            projectId: 'proj1',
            currency: 'USD',
            onBudgetChanged: onChanged,
          ),
        ),
      ),
    );
  }

  testWidgets('renders ledger entries fetched from the repo', (tester) async {
    final t = _RecordingTransport([
      _entry(id: 'be1', amount: 200.0, source: 'client deposit'),
      _entry(id: 'be2', amount: -50.0, source: 'correction', kind: 'edit'),
    ]);
    await tester.pumpWidget(host(t));
    await tester.pumpAndSettle();

    // It fetched on mount.
    expect(t.calls.first.method, 'GET');
    expect(t.calls.first.path, '/api/budgets/projects/proj1/budget-entries');

    // Credit + edit rows both render with their labels and sources.
    expect(find.text('Budget added'), findsOneWidget);
    expect(find.text('Budget edited'), findsOneWidget);
    expect(find.text('client deposit'), findsOneWidget);
    expect(find.text('+ \$200'), findsOneWidget);
    expect(find.text('− \$50'), findsOneWidget);
  });

  testWidgets('Add-to-budget posts to the repo and fires onBudgetChanged',
      (tester) async {
    final t = _RecordingTransport([]);
    var fired = 0;
    await tester.pumpWidget(host(t, onChanged: () async {
      fired++;
    }));
    await tester.pumpAndSettle();

    await tester.enterText(
        find.byKey(const Key('budget-add-amount')), '300');
    await tester.enterText(
        find.byKey(const Key('budget-add-source')), 'grant');
    await tester.tap(find.byKey(const Key('budget-add-submit')));
    await tester.pumpAndSettle();

    final post = t.calls.firstWhere((c) => c.method == 'POST');
    expect(post.path, '/api/budgets/projects/proj1/budget-entries');
    expect(post.body, containsPair('amount', 300.0));
    expect(post.body, containsPair('source', 'grant'));
    // Parent refresh hook fired so the budget bar can re-sync.
    expect(fired, 1);
  });

  testWidgets('Delete confirms then deletes via the repo', (tester) async {
    final t = _RecordingTransport([_entry(id: 'be1')]);
    await tester.pumpWidget(host(t));
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('Delete entry').first);
    await tester.pumpAndSettle();
    // Nothing deleted until the confirm dialog is accepted.
    expect(t.calls.any((c) => c.method == 'DELETE'), isFalse);

    await tester.tap(find.text('Delete'));
    await tester.pumpAndSettle();

    final del = t.calls.firstWhere((c) => c.method == 'DELETE');
    expect(del.path, '/api/budgets/entries/be1');
  });

  testWidgets('shows an error state with Retry when the load fails',
      (tester) async {
    final t = _ThrowingTransport();
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          budgetsRepositoryProvider.overrideWithValue(BudgetsRepository(t)),
        ],
        child: MaterialApp(
          theme: buildAppTheme(),
          home: const Scaffold(
            body: BudgetLogSheet(projectId: 'proj1', currency: 'USD'),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Retry'), findsOneWidget);
  });
}

class _ThrowingTransport implements BudgetsTransport {
  @override
  Future<Map<String, dynamic>> getJson(String path,
          {Map<String, dynamic>? queryParams}) async =>
      throw Exception('boom');
  @override
  Future<Map<String, dynamic>> postJson(
          String path, Map<String, dynamic> body) async =>
      throw Exception('boom');
  @override
  Future<Map<String, dynamic>> patchJson(
          String path, Map<String, dynamic> body) async =>
      throw Exception('boom');
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async =>
      throw Exception('boom');
}
