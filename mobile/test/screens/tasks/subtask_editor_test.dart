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
}) => MaterialApp(
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
    testWidgets('shows a formatted total for a sub-task with an expense', (
      tester,
    ) async {
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

    testWidgets('is absent when the total is zero (present key, no money)', (
      tester,
    ) async {
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
      await tester.pumpWidget(
        _host(expenseTotals: const {'s1': 9.0}, expenseCurrency: 'EUR'),
      );
      await tester.pump();

      expect(find.text('€9'), findsOneWidget);
    });

    testWidgets('sums correctly when a sub-task has multiple expenses '
        '(caller pre-sums into the map — this just renders it)', (
      tester,
    ) async {
      await tester.pumpWidget(_host(expenseTotals: const {'s1': 47.25}));
      await tester.pump();

      expect(find.text('\$47.25'), findsOneWidget);
    });

    testWidgets(
      'coexists with the comment badge on the same row without collision',
      (tester) async {
        await tester.pumpWidget(
          MaterialApp(
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
          ),
        );
        await tester.pump();

        expect(
          find.byKey(const ValueKey('subtask-expense-s1')),
          findsOneWidget,
        );
        expect(
          find.byKey(const ValueKey('subtask-comments-s1')),
          findsOneWidget,
        );
      },
    );
  });

  group(
    'SubtaskEditor existing call sites are unaffected by the new params',
    () {
      testWidgets('renders normally with neither expenseTotals nor '
          'expenseCurrency supplied (defaults only)', (tester) async {
        await tester.pumpWidget(
          MaterialApp(
            theme: buildAppTheme(),
            home: Scaffold(
              body: SubtaskEditor(subtasks: _subtasks, onChanged: (_) {}),
            ),
          ),
        );
        await tester.pump();

        expect(find.text('Buy flour'), findsOneWidget);
        expect(find.text('Preheat oven'), findsOneWidget);
        expect(find.byIcon(Icons.attach_money_rounded), findsNothing);
      });

      testWidgets('with onAddExpense omitted the money chip stays DISPLAY-ONLY '
          '(a plain Padding, no tap target) — add_task_sheet / task_row behave '
          'exactly as before', (tester) async {
        await tester.pumpWidget(_host(expenseTotals: const {'s1': 5.0}));
        await tester.pump();

        expect(
          tester.widget(find.byKey(const ValueKey('subtask-expense-s1'))),
          isA<Padding>(),
        );
      });
    },
  );

  group('SubtaskEditor add-expense affordance', () {
    Widget hostWithAdd({
      List<Subtask> subtasks = _subtasks,
      Set<String> savedSubtaskIds = const {'s1', 's2'},
      Map<String, double> expenseTotals = const {},
      ValueChanged<String>? onAddExpense,
    }) => MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        body: SubtaskEditor(
          subtasks: subtasks,
          onChanged: (_) {},
          savedSubtaskIds: savedSubtaskIds,
          expenseTotals: expenseTotals,
          onAddExpense: onAddExpense ?? (_) {},
        ),
      ),
    );

    testWidgets(
      'every SAVED sub-task gets a money affordance even with no expenses '
      'yet — it is how you ADD one, not just how you read one',
      (tester) async {
        await tester.pumpWidget(hostWithAdd());
        await tester.pump();

        expect(
          find.byKey(const ValueKey('subtask-expense-s1')),
          findsOneWidget,
        );
        expect(
          find.byKey(const ValueKey('subtask-expense-s2')),
          findsOneWidget,
        );
        expect(find.byIcon(Icons.attach_money_rounded), findsNWidgets(2));
      },
    );

    testWidgets('tapping fires onAddExpense with THAT sub-task id', (
      tester,
    ) async {
      final tapped = <String>[];
      await tester.pumpWidget(hostWithAdd(onAddExpense: tapped.add));
      await tester.pump();

      await tester.tap(find.byKey(const ValueKey('subtask-expense-s2')));
      await tester.pump();

      expect(tapped, ['s2']);
    });

    testWidgets(
      'an UNSAVED sub-task (absent from savedSubtaskIds) gets NO affordance '
      "— an expense's subtask_id can only point at a saved sub-task",
      (tester) async {
        await tester.pumpWidget(
          hostWithAdd(
            subtasks: const [
              Subtask(id: 's1', title: 'Buy flour', done: false),
              Subtask(id: 'draft', title: 'Not saved yet', done: false),
            ],
            savedSubtaskIds: const {'s1'},
          ),
        );
        await tester.pump();

        expect(
          find.byKey(const ValueKey('subtask-expense-s1')),
          findsOneWidget,
        );
        expect(
          find.byKey(const ValueKey('subtask-expense-draft')),
          findsNothing,
        );
        expect(find.byIcon(Icons.attach_money_rounded), findsOneWidget);
      },
    );

    testWidgets('shows the running total beside the icon once money exists', (
      tester,
    ) async {
      await tester.pumpWidget(hostWithAdd(expenseTotals: const {'s1': 12.5}));
      await tester.pump();

      expect(find.text('\$12.50'), findsOneWidget);
      // s2 is saved but has no money — bare icon, no amount text.
      expect(find.byKey(const ValueKey('subtask-expense-s2')), findsOneWidget);
    });

    testWidgets('the tappable affordance is a real tap target', (tester) async {
      await tester.pumpWidget(hostWithAdd());
      await tester.pump();

      expect(
        tester.widget(find.byKey(const ValueKey('subtask-expense-s1'))),
        isA<GestureDetector>(),
      );
    });
  });

  // ── Timestamp stamping ────────────────────────────────────────────────────
  //
  // Stamping lives HERE, not in the model: the editor is the only place that
  // knows a row was just born or just ticked. The server enforces the same
  // invariant independently, so the two must agree — a value the client
  // already set has to survive the round-trip untouched rather than be
  // re-stamped on arrival.
  group('SubtaskEditor timestamp stamping', () {
    const fixedNow = '2026-08-04T12:00:00.000000Z';

    Widget hostStamping({
      required List<Subtask> subtasks,
      required ValueChanged<List<Subtask>> onChanged,
      String now = fixedNow,
    }) => MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        body: SubtaskEditor(
          subtasks: subtasks,
          onChanged: onChanged,
          nowIso: _fixed(now),
        ),
      ),
    );

    testWidgets('creating a sub-task stamps createdAt (and leaves '
        'completedAt null)', (tester) async {
      List<Subtask>? emitted;
      await tester.pumpWidget(
        hostStamping(subtasks: const [], onChanged: (v) => emitted = v),
      );
      await tester.pump();

      await tester.enterText(
        find.byKey(const Key('subtask-add-field')),
        'Wash the car',
      );
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pump();

      expect(emitted, hasLength(1));
      expect(emitted!.single.title, 'Wash the car');
      expect(emitted!.single.done, isFalse);
      expect(emitted!.single.createdAt, fixedNow);
      expect(emitted!.single.completedAt, isNull);
    });

    testWidgets('ticking a sub-task stamps completedAt and preserves the '
        'original createdAt', (tester) async {
      List<Subtask>? emitted;
      await tester.pumpWidget(
        hostStamping(
          subtasks: const [
            Subtask(
              id: 's1',
              title: 'Buy flour',
              done: false,
              createdAt: '2026-08-01T08:00:00.000000+00:00',
            ),
          ],
          onChanged: (v) => emitted = v,
        ),
      );
      await tester.pump();

      await tester.tap(find.byKey(const ValueKey('subtask-toggle-s1')));
      await tester.pump();

      expect(emitted!.single.done, isTrue);
      expect(emitted!.single.completedAt, fixedNow);
      expect(emitted!.single.createdAt, '2026-08-01T08:00:00.000000+00:00');
    });

    testWidgets('UN-ticking clears completedAt back to null', (tester) async {
      List<Subtask>? emitted;
      await tester.pumpWidget(
        hostStamping(
          subtasks: const [
            Subtask(
              id: 's1',
              title: 'Buy flour',
              done: true,
              createdAt: '2026-08-01T08:00:00.000000+00:00',
              completedAt: '2026-08-02T09:30:00.000000+00:00',
            ),
          ],
          onChanged: (v) => emitted = v,
        ),
      );
      await tester.pump();

      await tester.tap(find.byKey(const ValueKey('subtask-toggle-s1')));
      await tester.pump();

      expect(emitted!.single.done, isFalse);
      expect(emitted!.single.completedAt, isNull);
      expect(emitted!.single.createdAt, '2026-08-01T08:00:00.000000+00:00');
    });

    testWidgets('a LEGACY row (no createdAt) is not backfilled when ticked — '
        'only completedAt is stamped', (tester) async {
      List<Subtask>? emitted;
      await tester.pumpWidget(
        hostStamping(
          subtasks: const [
            Subtask(id: 's1', title: 'Ancient', done: false),
          ],
          onChanged: (v) => emitted = v,
        ),
      );
      await tester.pump();

      await tester.tap(find.byKey(const ValueKey('subtask-toggle-s1')));
      await tester.pump();

      expect(emitted!.single.createdAt, isNull);
      expect(emitted!.single.completedAt, fixedNow);
    });

    testWidgets('renaming a sub-task touches NEITHER timestamp', (
      tester,
    ) async {
      List<Subtask>? emitted;
      await tester.pumpWidget(
        hostStamping(
          subtasks: const [
            Subtask(
              id: 's1',
              title: 'Old name',
              done: true,
              createdAt: '2026-08-01T08:00:00.000000+00:00',
              completedAt: '2026-08-02T09:30:00.000000+00:00',
            ),
          ],
          onChanged: (v) => emitted = v,
        ),
      );
      await tester.pump();

      await tester.tap(find.byKey(const ValueKey('subtask-text-s1')));
      await tester.pump();
      await tester.enterText(
        find.byKey(const ValueKey('subtask-edit-s1')),
        'New name',
      );
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pump();

      expect(emitted!.single.title, 'New name');
      expect(emitted!.single.createdAt, '2026-08-01T08:00:00.000000+00:00');
      expect(emitted!.single.completedAt, '2026-08-02T09:30:00.000000+00:00');
    });

    testWidgets('toggling one row leaves the OTHER rows byte-identical', (
      tester,
    ) async {
      const untouched = Subtask(
        id: 's2',
        title: 'Preheat oven',
        done: true,
        createdAt: '2026-07-30T07:00:00.000000+00:00',
        completedAt: '2026-07-30T08:00:00.000000+00:00',
      );
      List<Subtask>? emitted;
      await tester.pumpWidget(
        hostStamping(
          subtasks: const [
            Subtask(id: 's1', title: 'Buy flour', done: false),
            untouched,
          ],
          onChanged: (v) => emitted = v,
        ),
      );
      await tester.pump();

      await tester.tap(find.byKey(const ValueKey('subtask-toggle-s1')));
      await tester.pump();

      expect(emitted!.firstWhere((s) => s.id == 's2'), untouched);
    });

    testWidgets('the default nowIso (no injection) mints a real, parseable '
        'instant — the production path, not just the test double', (
      tester,
    ) async {
      List<Subtask>? emitted;
      await tester.pumpWidget(
        MaterialApp(
          theme: buildAppTheme(),
          home: Scaffold(
            body: SubtaskEditor(
              subtasks: const [],
              onChanged: (v) => emitted = v,
            ),
          ),
        ),
      );
      await tester.pump();

      await tester.enterText(
        find.byKey(const Key('subtask-add-field')),
        'Real clock',
      );
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pump();

      final created = emitted!.single.createdAt;
      expect(created, isNotNull);
      final parsed = DateTime.tryParse(created!);
      expect(parsed, isNotNull, reason: 'must survive Subtask.fromMap');
      expect(parsed!.isUtc, isTrue);
    });
  });
}

/// A frozen clock for the stamping tests. A top-level function (rather than a
/// closure built inline) keeps `SubtaskEditor`'s `nowIso` argument a plain
/// value the const constructor is happy with.
String Function() _fixed(String iso) => () => iso;
