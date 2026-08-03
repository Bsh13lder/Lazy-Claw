// Widget tests for the Add-Task sheet's expense quick-typing chip + the
// floating square save.
//
// The chip SPENDS THE USER'S MONEY, so the behaviours pinned here are all
// about it never firing on its own:
//   * armed by default ONLY with an explicit currency marker,
//   * a bare number offers the chip but leaves it un-armed,
//   * a manual toggle is sticky and survives further typing,
//   * no project → visibly disabled with a stated reason,
//   * the amount rides out on the result ONLY when armed.
//
// Uses the same safe host pattern as the other add-task sheet tests: the real
// showAddTaskSheet helper inside a ProviderScope with no live notifiers — the
// sheet only returns a value, which the host captures.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/smart_add_parser.dart' show SmartTokenKind;
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/screens/tasks/add_task_expense_chip.dart';
import 'package:lazyclaw_mobile/screens/tasks/add_task_sheet.dart';
import 'package:lazyclaw_mobile/screens/tasks/smart_add_controller.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

Project _project(String id, String name) =>
    Project(id: id, name: name, budget: 0, currency: 'EUR', status: 'active');

void main() {
  ({String? title, double? expenseAmount, String? dueDate, String? category})?
  captured;

  Widget host({List<Project> projects = const []}) => ProviderScope(
    child: MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        body: Center(
          child: Builder(
            builder: (ctx) => ElevatedButton(
              onPressed: () async {
                final r = await showAddTaskSheet(ctx, projects: projects);
                captured = r == null
                    ? null
                    : (
                        title: r.title,
                        expenseAmount: r.expenseAmount,
                        dueDate: r.dueDate,
                        category: r.category,
                      );
              },
              child: const Text('open'),
            ),
          ),
        ),
      ),
    ),
  );

  Future<void> open(
    WidgetTester tester, {
    List<Project> projects = const [],
  }) async {
    await tester.pumpWidget(host(projects: projects));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
  }

  Future<void> type(WidgetTester tester, String text) async {
    await tester.enterText(find.byType(TextField).first, text);
    await tester.pump();
  }

  Finder chip() => find.byKey(kAddTaskExpenseChipKey);

  /// The chip's armed state, read off the rendered [LzChip].
  bool armed(WidgetTester tester) => tester.widget<LzChip>(chip()).selected;

  /// Whether the chip is interactive (a disabled chip has no onTap).
  bool enabled(WidgetTester tester) =>
      tester.widget<LzChip>(chip()).onTap != null;

  Future<void> submit(WidgetTester tester) async {
    await tester.tap(find.byKey(kAddTaskSubmitKey));
    await tester.pumpAndSettle();
  }

  setUp(() => captured = null);

  testWidgets('no amount typed → no expense chip at all', (tester) async {
    await open(tester);
    await type(tester, 'buy milk tomorrow');
    expect(chip(), findsNothing);
  });

  testWidgets('currency marker arms the chip by default', (tester) async {
    await open(tester);
    await type(tester, 'buy paint 40 eur #home tomorrow');

    expect(chip(), findsOneWidget);
    expect(armed(tester), isTrue);
    expect(enabled(tester), isTrue);
    // The label states the amount so the user can see what will be spent.
    expect(tester.widget<LzChip>(chip()).label, contains('40'));
  });

  testWidgets('a bare number offers the chip but leaves it un-armed', (
    tester,
  ) async {
    await open(tester);
    await type(tester, 'buy 2 apples #home');

    expect(chip(), findsOneWidget);
    expect(armed(tester), isFalse);
  });

  testWidgets('no project → chip disabled with a stated reason', (
    tester,
  ) async {
    await open(tester);
    await type(tester, 'buy paint 40 eur');

    expect(chip(), findsOneWidget);
    expect(enabled(tester), isFalse);
    expect(armed(tester), isFalse);
    expect(find.byKey(kAddTaskExpenseReasonKey), findsOneWidget);
  });

  testWidgets('a manual toggle sticks across further typing', (tester) async {
    await open(tester);
    // Armed by default (currency marker present).
    await type(tester, 'buy paint 40 eur #home');
    expect(armed(tester), isTrue);

    // Turn it OFF...
    await tester.tap(chip());
    await tester.pump();
    expect(armed(tester), isFalse);

    // ...and keep typing. A re-parse must NOT flip it back on.
    await type(tester, 'buy paint 40 eur #home tomorrow');
    expect(armed(tester), isFalse);
    expect(find.byKey(kAddTaskExpenseReasonKey), findsNothing);
  });

  testWidgets('a bare number toggled ON stays on', (tester) async {
    await open(tester);
    await type(tester, 'buy 2 apples #home');
    expect(armed(tester), isFalse);

    await tester.tap(chip());
    await tester.pump();
    expect(armed(tester), isTrue);

    await type(tester, 'buy 2 apples #home tomorrow');
    expect(armed(tester), isTrue);
  });

  testWidgets(
    'submit with the chip armed carries the amount and a clean title',
    (tester) async {
      await open(tester);
      await type(tester, 'buy paint 40 eur #home tomorrow');
      await submit(tester);

      expect(captured, isNotNull);
      expect(captured!.expenseAmount, 40);
      // The amount token is consumed, exactly like every other recognized
      // token in this field.
      expect(captured!.title, 'buy paint');
      // ...and the date/project parse is untouched by the amount matcher.
      expect(captured!.category, 'home');
      expect(captured!.dueDate, isNotNull);
    },
  );

  testWidgets(
    'submit with the chip OFF files no expense and keeps the digits',
    (tester) async {
      await open(tester);
      await type(tester, 'buy paint 40 eur #home tomorrow');
      await tester.tap(chip());
      await tester.pump();
      await submit(tester);

      expect(captured, isNotNull);
      expect(captured!.expenseAmount, isNull);
      // An un-consumed token is NOT stripped — the money stays in the title.
      expect(captured!.title, 'buy paint 40 eur');
    },
  );

  testWidgets('a bare number is never spent without an explicit tap', (
    tester,
  ) async {
    await open(tester);
    await type(tester, 'buy 2 apples #home tomorrow');
    await submit(tester);

    expect(captured!.expenseAmount, isNull);
    expect(captured!.title, 'buy 2 apples');
  });

  testWidgets('an un-armed amount is not highlighted in the field', (
    tester,
  ) async {
    // Highlight == consumed == stripped is this field's existing contract for
    // every token family; an un-armed amount is none of those. Tapping the
    // chip is therefore visible in the field itself.
    await open(tester);
    // NOTE the `#home`: without a project the chip is disabled and cannot be
    // tapped at all (covered separately above).
    await type(tester, 'buy 2 apples #home');

    final ctrl =
        tester.widget<TextField>(find.byType(TextField).first).controller!
            as SmartAddController;

    Iterable<SmartTokenKind> kinds() => ctrl.tokens.map((t) => t.kind);

    expect(armed(tester), isFalse);
    expect(
      kinds(),
      isNot(contains(SmartTokenKind.amount)),
      reason: 'an un-armed amount must not paint a token span',
    );
    // The project token is still highlighted — only the amount is withheld.
    expect(kinds(), contains(SmartTokenKind.project));

    await tester.tap(chip());
    await tester.pump();
    expect(kinds(), contains(SmartTokenKind.amount));
    expect(kinds(), contains(SmartTokenKind.project));
  });

  testWidgets('picking a project arms a currency-marked amount immediately', (
    tester,
  ) async {
    // Gaining a project is the OTHER way the chip can flip to armed. The
    // highlight has to follow in the same frame — otherwise "highlighted ==
    // will be filed" is only true until the next keystroke.
    await open(tester, projects: [_project('p1', 'home')]);
    await type(tester, 'buy paint 40 eur');
    expect(enabled(tester), isFalse, reason: 'no project yet');

    await tester.tap(find.byKey(const Key('add-task-project')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('project-pick-p1')));
    await tester.pumpAndSettle();

    expect(enabled(tester), isTrue);
    expect(armed(tester), isTrue);

    final ctrl =
        tester.widget<TextField>(find.byType(TextField).first).controller!
            as SmartAddController;
    expect(
      ctrl.tokens.map((t) => t.kind),
      contains(SmartTokenKind.amount),
      reason: 'the highlight must arm in the same frame as the chip',
    );
  });

  testWidgets('picking a `/` suggestion re-derives the amount span', (
    tester,
  ) async {
    // The suggestion strip REWRITES the title (it deletes the `/token` out of
    // the middle of the string). Every character after that point shifts, so a
    // carried-over amount span would quote the wrong characters — and the
    // stripped title on submit would cut the wrong slice out.
    //
    // Taps an EXISTING-project row, not the "Create project" row: the latter
    // goes through BudgetsNotifier and needs a real overridden database, which
    // this host deliberately doesn't have.
    await open(tester, projects: [_project('p1', 'misc')]);
    await type(tester, 'buy /misc paint 40 eur');
    expect(armed(tester), isTrue);

    await tester.tap(find.byKey(const ValueKey('project-suggest-misc')));
    await tester.pumpAndSettle();

    // Still armed, still 40 — and the title strip now lands correctly.
    expect(chip(), findsOneWidget);
    expect(armed(tester), isTrue);
    await submit(tester);
    expect(captured!.expenseAmount, 40);
    expect(captured!.title, 'buy paint');
    expect(captured!.category, 'misc');
  });

  group('floating square save', () {
    testWidgets('replaces the old full-width bottom button', (tester) async {
      await open(tester);
      expect(find.byKey(kAddTaskSubmitKey), findsOneWidget);
      // No parallel widgets: the old "Add Task" LzButton is gone, not hidden.
      expect(
        find.byWidgetPredicate((w) => w is LzButton && w.label == 'Add Task'),
        findsNothing,
      );
    });

    testWidgets('is reachable without scrolling the sheet', (tester) async {
      await open(tester);
      // A tall sheet: sub-tasks + recurrence + reminder push the old bottom
      // button off-screen. The floating save must still be hit-testable with
      // no ensureVisible() call.
      await type(tester, 'pay rent tomorrow 5pm every monday #home');
      await tester.tap(find.byKey(kAddTaskSubmitKey));
      await tester.pumpAndSettle();
      expect(captured, isNotNull);
    });
  });
}
