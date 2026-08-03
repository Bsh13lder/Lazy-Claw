// Widget tests for SubtaskEditor's expense "money chip" — the small
// icon + formatted total shown on a sub-task row that has at least one
// expense linked to it. Mirrors the existing 💬 comment-badge pattern:
// display-only, gated on a map keyed by sub-task id, absent by default so
// every pre-existing call site (task detail sheet, add-task sheet, task row)
// compiles and renders unchanged.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/subtask.dart';
import 'package:lazyclaw_mobile/screens/tasks/subtask_editor.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

const _subtasks = [
  Subtask(id: 's1', title: 'Buy flour', done: false),
  Subtask(id: 's2', title: 'Preheat oven', done: false),
];

Widget _host({
  List<Subtask> subtasks = _subtasks,
  Map<String, double> expenseTotals = const {},
  String expenseCurrency = 'USD',
}) =>
    MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        body: SubtaskEditor(
          subtasks: subtasks,
          onChanged: (_) {},
          expenseTotals: expenseTotals,
          expenseCurrency: expenseCurrency,
        ),
      ),
    );

void main() {
  group('SubtaskEditor money chip', () {
    testWidgets('shows a formatted total for a sub-task with an expense',
        (tester) async {
      await tester.pumpWidget(_host(expenseTotals: const {'s1': 12.5}));
      await tester.pump();

      expect(find.byKey(const ValueKey('subtask-expense-s1')), findsOneWidget);
      expect(find.text('\$12.50'), findsOneWidget);
      // s2 has no entry in the map — no chip.
      expect(find.byKey(const ValueKey('subtask-expense-s2')), findsNothing);
    });

    testWidgets('is absent for every sub-task when expenseTotals is empty '
        '(the default)', (tester) async {
      await tester.pumpWidget(_host());
      await tester.pump();

      expect(find.byIcon(Icons.attach_money_rounded), findsNothing);
    });

    testWidgets('is absent when the total is zero (present key, no money)',
        (tester) async {
      await tester.pumpWidget(_host(expenseTotals: const {'s1': 0.0}));
      await tester.pump();

      expect(find.byKey(const ValueKey('subtask-expense-s1')), findsNothing);
    });

    testWidgets('formats a whole number without decimals, drops trailing '
        'zeros (fmtMoney behavior, not re-implemented)', (tester) async {
      await tester.pumpWidget(_host(expenseTotals: const {'s1': 20.0}));
      await tester.pump();

      expect(find.text('\$20'), findsOneWidget);
    });

    testWidgets('respects the currency symbol', (tester) async {
      await tester.pumpWidget(_host(
        expenseTotals: const {'s1': 9.0},
        expenseCurrency: 'EUR',
      ));
      await tester.pump();

      expect(find.text('€9'), findsOneWidget);
    });

    testWidgets('sums correctly when a sub-task has multiple expenses '
        '(caller pre-sums into the map — this just renders it)',
        (tester) async {
      await tester.pumpWidget(_host(expenseTotals: const {'s1': 47.25}));
      await tester.pump();

      expect(find.text('\$47.25'), findsOneWidget);
    });

    testWidgets(
        'coexists with the comment badge on the same row without collision',
        (tester) async {
      await tester.pumpWidget(MaterialApp(
        theme: buildAppTheme(),
        home: Scaffold(
          body: SubtaskEditor(
            subtasks: _subtasks,
            onChanged: (_) {},
            expenseTotals: const {'s1': 5.0},
            commentCounts: const {'s1': 2},
            onOpenComments: (_) {},
          ),
        ),
      ));
      await tester.pump();

      expect(find.byKey(const ValueKey('subtask-expense-s1')), findsOneWidget);
      expect(find.byKey(const ValueKey('subtask-comments-s1')), findsOneWidget);
    });
  });

  group('SubtaskEditor existing call sites are unaffected by the new params',
      () {
    testWidgets('renders normally with neither expenseTotals nor '
        'expenseCurrency supplied (defaults only)', (tester) async {
      await tester.pumpWidget(MaterialApp(
        theme: buildAppTheme(),
        home: Scaffold(
          body: SubtaskEditor(
            subtasks: _subtasks,
            onChanged: (_) {},
          ),
        ),
      ));
      await tester.pump();

      expect(find.text('Buy flour'), findsOneWidget);
      expect(find.text('Preheat oven'), findsOneWidget);
      expect(find.byIcon(Icons.attach_money_rounded), findsNothing);
    });
  });
}
