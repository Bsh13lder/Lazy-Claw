// Widget tests for the ledger CreditRow — the green "+ €X Budget added" row that
// interleaves budget top-ups among the expense (debit) rows.
//
// CreditRow is a pure StatelessWidget over a LedgerItem — no notifier, no repo,
// no network — so a single pump (never pumpAndSettle on a live notifier) fully
// renders it.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/budget_entry.dart';
import 'package:lazyclaw_mobile/screens/expenses/budget_math.dart';
import 'package:lazyclaw_mobile/screens/expenses/credit_row.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

LedgerItem _credit({
  double amount = 500,
  String currency = 'EUR',
  String? source,
}) {
  final entry = BudgetEntry(
    id: 'be1',
    projectId: 'p1',
    amount: amount,
    currency: currency,
    source: source,
    kind: 'credit',
    createdAt: '2026-06-05T10:00:00Z',
  );
  return LedgerItem(
    date: DateTime.parse('2026-06-05T10:00:00Z'),
    amount: amount,
    label: 'Budget added',
    isCredit: true,
    currency: currency,
    source: source,
    entry: entry,
  );
}

Widget _host(LedgerItem item) => MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(body: CreditRow(item: item)),
    );

void main() {
  testWidgets('renders the "Budget added" label and a signed + amount',
      (tester) async {
    await tester.pumpWidget(_host(_credit(amount: 500, currency: 'EUR')));
    await tester.pump();

    expect(find.text('Budget added'), findsOneWidget);
    expect(find.text('+ €500'), findsOneWidget);
  });

  testWidgets('shows the source note when present', (tester) async {
    await tester
        .pumpWidget(_host(_credit(source: 'ClubBay', currency: 'EUR')));
    await tester.pump();

    expect(find.text('Budget added'), findsOneWidget);
    expect(find.textContaining('ClubBay'), findsOneWidget);
  });
}
