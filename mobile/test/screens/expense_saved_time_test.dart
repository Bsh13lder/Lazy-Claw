// Tests for the expense "saved at" (created_at) surface:
//   * the pure `formatSavedAt` formatter, and
//   * the ExpenseRow caption that renders it.
//
// Time assertions use no-`Z` (local) ISO strings so `toLocal()` is a no-op and
// the clock reads deterministically regardless of the test machine's timezone.
// Dates use a past year so `friendlyDate` returns an absolute "Mon D, YYYY"
// label instead of the relative "Today"/"Yesterday".

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/expense.dart';
import 'package:lazyclaw_mobile/screens/expenses/expense_row.dart';
import 'package:lazyclaw_mobile/screens/expenses/money_helpers.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

Expense _expense({
  required String id,
  String? createdAt,
  String? spentAt,
}) =>
    Expense(
      id: id,
      projectId: 'p1',
      amount: 12.5,
      currency: 'USD',
      description: 'Coffee beans',
      status: 'posted',
      createdAt: createdAt,
      spentAt: spentAt,
    );

Widget _host(Expense e) => MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        body: ExpenseRow(
          expense: e,
          projects: const [],
          pendingSync: false,
          onDelete: null,
          showProject: false,
        ),
      ),
    );

void main() {
  group('formatSavedAt', () {
    test('formats a full local ISO timestamp as "Mon D, YYYY · h:mm AM/PM"', () {
      expect(formatSavedAt('2020-01-15T14:32:00'), 'Jan 15, 2020 · 2:32 PM');
    });

    test('formats a morning time with AM', () {
      expect(formatSavedAt('2020-01-15T09:05:00'), 'Jan 15, 2020 · 9:05 AM');
    });

    test('formats midnight as 12:00 AM', () {
      expect(formatSavedAt('2020-01-15T00:00:00'), 'Jan 15, 2020 · 12:00 AM');
    });

    test('returns null for null', () {
      expect(formatSavedAt(null), isNull);
    });

    test('returns null for an empty string', () {
      expect(formatSavedAt(''), isNull);
    });

    test('returns null for an unparseable string', () {
      expect(formatSavedAt('not-a-date'), isNull);
    });
  });

  group('ExpenseRow saved-time caption', () {
    testWidgets('shows the saved time when createdAt is set', (tester) async {
      await tester.pumpWidget(
        _host(_expense(id: 'e1', createdAt: '2020-01-15T14:32:00')),
      );
      await tester.pump();

      expect(find.byKey(const ValueKey('expense-row-saved-e1')),
          findsOneWidget);
      expect(find.textContaining('2:32 PM'), findsOneWidget);
    });

    testWidgets('falls back to spentAt when createdAt is null', (tester) async {
      await tester.pumpWidget(
        _host(_expense(id: 'e2', spentAt: '2020-01-15T09:05:00')),
      );
      await tester.pump();

      expect(find.byKey(const ValueKey('expense-row-saved-e2')),
          findsOneWidget);
      expect(find.textContaining('9:05 AM'), findsOneWidget);
    });

    testWidgets('renders no caption (and does not crash) with no timestamps',
        (tester) async {
      await tester.pumpWidget(_host(_expense(id: 'e3')));
      await tester.pump();

      expect(find.byKey(const ValueKey('expense-row-saved-e3')), findsNothing);
      // The row itself still renders fine.
      expect(find.text('Coffee beans'), findsOneWidget);
    });
  });
}
