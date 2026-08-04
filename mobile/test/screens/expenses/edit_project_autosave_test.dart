// Auto-save behaviour of the PROJECT edit sheet — the third edit-in-place
// surface with the "change three fields, swipe away, lose all three" problem.
//
// It differs from the task/expense sheets in one way that matters: it has no
// single patch call. Rename / budget / colour / dates are four independent
// callbacks, so "write only what changed" is not an optimisation here, it is
// the only correct behaviour — and each one needs its OWN baseline, advanced
// as it succeeds, or a value that is auto-saved and then reverted never gets
// written back.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/autosave.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/screens/expenses/edit_project_sheet.dart';
import 'package:lazyclaw_mobile/screens/expenses/project_color_picker.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:lazyclaw_mobile/widgets/autosave_indicator.dart';

const _project = Project(
  id: 'p-1',
  name: 'Marketing',
  budget: 500,
  color: '#10B981',
  currency: 'USD',
  status: 'active',
);

/// Records every callback the sheet fires, and can be made to fail.
class _Recorder {
  final List<String> renames = [];
  final List<double> budgets = [];
  final List<String> colors = [];
  final List<List<String>> dates = [];
  int deletes = 0;

  bool renameSucceeds = true;

  List<String> get all => [
    ...renames.map((n) => 'rename:$n'),
    ...budgets.map((b) => 'budget:$b'),
    ...colors.map((c) => 'color:$c'),
    ...dates.map((d) => 'dates:${d.join(",")}'),
  ];
}

void main() {
  Future<_Recorder> open(
    WidgetTester tester, {
    Project project = _project,
  }) async {
    await tester.binding.setSurfaceSize(const Size(600, 1600));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final rec = _Recorder();
    await tester.pumpWidget(
      MaterialApp(
        theme: buildAppTheme(),
        home: Scaffold(
          body: SingleChildScrollView(
            child: EditProjectSheet(
              project: project,
              onRename: (name) async {
                rec.renames.add(name);
                return rec.renameSucceeds;
              },
              onSetBudget: (b) async {
                rec.budgets.add(b);
                return true;
              },
              onSetColor: (c) async {
                rec.colors.add(c);
                return true;
              },
              onDelete: () async {
                rec.deletes++;
                return true;
              },
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    return rec;
  }

  Future<void> settleAutosave(WidgetTester tester) async {
    await tester.pump(kAutosaveDebounce + const Duration(milliseconds: 50));
    await tester.pumpAndSettle();
  }

  testWidgets('opening and closing writes NOTHING', (tester) async {
    final rec = await open(tester);
    await tester.pumpWidget(const SizedBox());
    await tester.pump();

    expect(rec.all, isEmpty);
  });

  // The other two sheets baseline themselves off their own CONTROLLERS, so a
  // display rounding is invisible to their dirty gate. This one baselines off
  // the raw `Project` while the field shows `_formatBudget`'s 2dp rendering —
  // the one place where merely opening and closing can manufacture an edit.
  testWidgets('a stored budget with sub-cent precision still writes NOTHING '
      'on open+close — the field rounds it for display, and a display '
      'rounding is not a user edit', (tester) async {
    final rec = await open(
      tester,
      project: _project.copyWith(budget: 1234.567),
    );
    await tester.pumpWidget(const SizedBox());
    await tester.pump();

    expect(rec.all, isEmpty,
        reason: 'opening the sheet rendered 1234.567 as "1234.57"; writing '
            'that back would churn updated_at and, under last-write-wins '
            'sync, could clobber a real remote change');
  });

  testWidgets('renaming autosaves after the debounce — once, not per keystroke',
      (tester) async {
    final rec = await open(tester);

    for (final text in ['M', 'Ma', 'Mark', 'Market research']) {
      await tester.enterText(find.byKey(kEditProjectNameKey), text);
      await tester.pump(const Duration(milliseconds: 80));
    }
    expect(rec.renames, isEmpty, reason: 'still inside the debounce');

    await settleAutosave(tester);
    expect(rec.renames, ['Market research']);
    // Only the field that changed is written — the other three callbacks are
    // separate round-trips and must not fire for an untouched value.
    expect(rec.budgets, isEmpty);
    expect(rec.colors, isEmpty);
  });

  testWidgets('a value that is auto-saved and then REVERTED is written back — '
      'each callback needs its own advancing baseline', (tester) async {
    final rec = await open(tester);
    final nameField = find.byKey(kEditProjectNameKey);

    await tester.enterText(nameField, 'Renamed');
    await settleAutosave(tester);
    expect(rec.renames, ['Renamed']);

    await tester.enterText(find.byKey(kEditProjectNameKey), 'Marketing');
    await settleAutosave(tester);
    expect(rec.renames, ['Renamed', 'Marketing'],
        reason: 'reverting to the ORIGINAL is still a change from what is '
            'now stored');
  });

  testWidgets('picking a colour commits immediately', (tester) async {
    final rec = await open(tester);

    // The swatch row renders one GestureDetector per palette entry; take the
    // last, which is not the seeded selection.
    final swatches = find.descendant(
      of: find.byType(ProjectColorSwatches),
      matching: find.byType(GestureDetector),
    );
    expect(swatches, findsWidgets);
    await tester.tap(swatches.last);
    await tester.pump();

    expect(rec.colors, hasLength(1));
    expect(rec.renames, isEmpty);
  });

  testWidgets('a blank name is refused inline and nothing is written',
      (tester) async {
    final rec = await open(tester);

    await tester.enterText(find.byKey(kEditProjectNameKey), '');
    await settleAutosave(tester);

    expect(rec.all, isEmpty);
    expect(find.text('Project name is required'), findsOneWidget);
  });

  testWidgets('a junk budget is refused inline and nothing is written',
      (tester) async {
    final rec = await open(tester);

    await tester.enterText(find.byKey(kEditProjectBudgetKey), 'abc');
    await settleAutosave(tester);

    expect(rec.all, isEmpty);
    expect(find.text('Enter a valid amount'), findsOneWidget);
  });

  testWidgets('a pending debounce is FLUSHED on dismiss, not dropped',
      (tester) async {
    final rec = await open(tester);

    await tester.enterText(
      find.byKey(kEditProjectNameKey),
      'Typed then swiped away',
    );
    await tester.pump(const Duration(milliseconds: 50));
    expect(rec.renames, isEmpty);

    // Unmount WITHOUT advancing the clock: the debounce provably never fired.
    await tester.pumpWidget(const SizedBox());
    await tester.pump();

    expect(rec.renames, ['Typed then swiped away']);
  });

  testWidgets('a failing callback surfaces as "Save failed" and is retried on '
      'the next edit', (tester) async {
    final rec = await open(tester);
    rec.renameSucceeds = false;

    await tester.enterText(find.byKey(kEditProjectNameKey), 'Nope');
    await settleAutosave(tester);
    expect(find.text(kAutosaveFailedLabel), findsOneWidget);

    rec.renameSucceeds = true;
    await tester.enterText(find.byKey(kEditProjectNameKey), 'Yes');
    await settleAutosave(tester);

    expect(rec.renames, ['Nope', 'Yes']);
    expect(find.text(kAutosaveSavedLabel), findsOneWidget);
  });

  testWidgets('Delete still needs an explicit confirm', (tester) async {
    final rec = await open(tester);

    await tester.tap(find.text('Delete project'));
    await tester.pumpAndSettle();
    expect(rec.deletes, 0, reason: 'confirm dialog is up');

    await tester.tap(find.text('Delete').last);
    await tester.pumpAndSettle();
    expect(rec.deletes, 1);
  });
}
