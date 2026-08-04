// Auto-save behaviour of the EXPENSE detail (edit) sheet.
//
// Same shape as the task sheet's suite, and the same first priority: an
// open-and-close must produce ZERO writes. `updateExpense` lands in the
// encrypted cache + the sync outbox and bumps `updated_at`; sync is
// last-write-wins, so a spurious write is not merely wasteful, it can overwrite
// a real edit made on another device.
//
// This sheet's patch contract is the `*Set` null-vs-absent pair (see
// `BudgetsDao.applyLocalExpenseUpdate`): `taskIdSet`/`subtaskIdSet` are what
// distinguish "clear the link" from "leave it alone", and auto-save must not
// disturb either.

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/autosave.dart';
import 'package:lazyclaw_mobile/models/expense.dart';
import 'package:lazyclaw_mobile/screens/expenses/expense_detail_sheet.dart';
import 'package:lazyclaw_mobile/widgets/autosave_indicator.dart';

import 'expense_detail_harness.dart';

const _amountKey = Key('expense-detail-amount');
const _descKey = Key('expense-detail-desc');
const _vendorKey = Key('expense-detail-vendor');
const _projectKey = Key('expense-detail-project');
const _saveKey = Key('expense-detail-save');

const _sample = Expense(
  id: 'exp-42',
  projectId: 'proj-1',
  amount: 12.5,
  currency: 'USD',
  description: 'Coffee beans',
  vendor: 'Blue Bottle',
  status: 'posted',
  spentAt: '2026-06-05',
);

const _stepsJson = '[{"id":"s1","title":"Buy flour","done":false}]';

void main() {
  Future<StubBudgetsNotifier> open(
    WidgetTester tester, {
    Expense expense = _sample,
    List<dynamic> tasks = const [],
  }) async {
    await tester.binding.setSurfaceSize(const Size(600, 1600));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final budgets = makeExpenseBudgetsStub();
    await tester.pumpWidget(
      expenseSheetHost(
        budgets: budgets,
        tasks: makeExpenseTasksStub(tasks.cast()),
        expense: expense,
      ),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    return budgets;
  }

  /// Advance past the text debounce and let the write settle. `pumpAndSettle`
  /// alone only advances while frames are scheduled, and a bare `Timer`
  /// schedules none — a debounced save would look like it never happened.
  Future<void> settleAutosave(WidgetTester tester) async {
    await tester.pump(kAutosaveDebounce + const Duration(milliseconds: 50));
    await tester.pumpAndSettle();
  }

  /// Unmount WITHOUT advancing the clock, so a still-pending debounce
  /// provably has not fired and any write can only be the dismiss flush.
  Future<void> dismissWithoutAdvancingTime(WidgetTester tester) async {
    await tester.pumpWidget(const SizedBox());
    await tester.pump();
  }

  group('no edits', () {
    testWidgets('opening and closing writes NOTHING', (tester) async {
      final budgets = await open(tester);

      await tester.tapAt(const Offset(10, 10));
      await tester.pumpAndSettle();

      expect(budgets.updateCalls, isEmpty);
    });

    testWidgets('tapping Save with no edits writes nothing and closes',
        (tester) async {
      final budgets = await open(tester);

      await tester.ensureVisible(find.byKey(_saveKey));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(_saveKey));
      await tester.pumpAndSettle();

      expect(budgets.updateCalls, isEmpty);
      expect(find.byKey(_amountKey), findsNothing);
    });

    testWidgets('a GHOST sub-task link is reconciled on open WITHOUT writing '
        '— the sheet correcting its own stale state is not a user edit',
        (tester) async {
      const withGhost = Expense(
        id: 'exp-94',
        projectId: 'proj-1',
        taskId: 't1',
        subtaskId: 'ghost-subtask',
        amount: 5,
        currency: 'USD',
        description: 'Flour',
        status: 'posted',
      );
      final budgets = await open(
        tester,
        expense: withGhost,
        tasks: [
          makeLinkedTask('t1', 'Bake a cake',
              category: 'Marketing', steps: _stepsJson),
        ],
      );

      await settleAutosave(tester);
      expect(budgets.updateCalls, isEmpty);
      expect(
        tester
            .widget<DropdownButton<String>>(
                find.byKey(const Key('expense-detail-subtask')))
            .value,
        isNull,
      );
    });
  });

  group('debounced text', () {
    testWidgets('several keystrokes in the amount produce exactly ONE write',
        (tester) async {
      final budgets = await open(tester);

      for (final text in ['2', '20', '20.', '20.75']) {
        await tester.enterText(find.byKey(_amountKey), text);
        await tester.pump(const Duration(milliseconds: 80));
      }
      expect(budgets.updateCalls, isEmpty, reason: 'still inside the debounce');

      await settleAutosave(tester);

      expect(budgets.updateCalls, hasLength(1));
      expect(budgets.updateCalls.single['amount'], 20.75);
    });

    testWidgets('the vendor autosaves, and an emptied vendor rides as null',
        (tester) async {
      final budgets = await open(tester);

      await tester.enterText(find.byKey(_vendorKey), '');
      await settleAutosave(tester);

      expect(budgets.updateCalls, hasLength(1));
      expect(budgets.updateCalls.single['vendor'], isNull);
    });

    testWidgets('a cosmetic re-typing of the same amount does not write',
        (tester) async {
      final budgets = await open(tester);

      // 12.50 is 12.5 — the value did not change, only its spelling.
      await tester.enterText(find.byKey(_amountKey), '12.50');
      await settleAutosave(tester);

      expect(budgets.updateCalls, isEmpty);
    });
  });

  group('discrete controls', () {
    testWidgets('switching project commits immediately', (tester) async {
      final budgets = await open(tester);

      await tester.tap(find.byKey(_projectKey));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Operations').last);
      await tester.pumpAndSettle();

      expect(budgets.updateCalls, hasLength(1));
      expect(budgets.updateCalls.single['projectId'], 'proj-2');
      // A task belongs to one project, so the link resets — and the reset
      // must ride as an explicit clear, not as "leave it alone".
      expect(budgets.updateCalls.single['taskId'], isNull);
      expect(budgets.updateCalls.single['taskIdSet'], isTrue);
    });

    testWidgets('the date quick-pick commits immediately', (tester) async {
      final budgets = await open(tester);

      await tester.tap(find.text('Today'));
      await tester.pumpAndSettle();

      expect(budgets.updateCalls, hasLength(1));
      expect(budgets.updateCalls.single['spentAt'], isNot('2026-06-05'));
    });
  });

  group('dismiss', () {
    testWidgets('a pending debounce is FLUSHED on dismiss, not dropped',
        (tester) async {
      final budgets = await open(tester);

      await tester.enterText(find.byKey(_descKey), 'Typed then swiped away');
      await tester.pump(const Duration(milliseconds: 50));
      expect(budgets.updateCalls, isEmpty, reason: 'debounce still running');

      await dismissWithoutAdvancingTime(tester);

      expect(budgets.updateCalls, hasLength(1));
      expect(
        budgets.updateCalls.single['description'],
        'Typed then swiped away',
      );
    });
  });

  group('coalescing', () {
    testWidgets('edits landing during an in-flight save collapse into ONE '
        'follow-up carrying the final state', (tester) async {
      final budgets = await open(tester);
      final gate = Completer<void>();
      budgets.updateGate = gate;

      // Plain `pump` from here on, NOT pumpAndSettle: while a save is parked
      // the indicator renders a spinner, which schedules frames forever and
      // would hang settle.
      await tester.tap(find.text('Today'));
      await tester.pump();
      expect(budgets.updateCalls, hasLength(1), reason: 'first save in flight');

      await tester.enterText(find.byKey(_descKey), 'One');
      await tester.pump(kAutosaveDebounce + const Duration(milliseconds: 50));
      await tester.enterText(find.byKey(_descKey), 'Two');
      await tester.pump(kAutosaveDebounce + const Duration(milliseconds: 50));
      expect(budgets.updateCalls, hasLength(1),
          reason: 'no second write may start while one is in flight');

      budgets.updateGate = null;
      gate.complete();
      await tester.pumpAndSettle();

      expect(budgets.updateCalls, hasLength(2));
      expect(budgets.updateCalls.last['description'], 'Two');
    });
  });

  group('invalid state', () {
    testWidgets('an empty description is never written over a good one',
        (tester) async {
      final budgets = await open(tester);

      await tester.enterText(find.byKey(_descKey), '');
      await settleAutosave(tester);

      expect(budgets.updateCalls, isEmpty);
      expect(find.text(kExpenseDescriptionRequiredError), findsOneWidget);
    });

    testWidgets('a non-numeric amount is refused inline and blocks the write',
        (tester) async {
      final budgets = await open(tester);

      await tester.enterText(find.byKey(_amountKey), 'abc');
      await settleAutosave(tester);

      expect(budgets.updateCalls, isEmpty);
      expect(find.text(kExpenseAmountInvalidError), findsOneWidget);
    });

    testWidgets('the held edit lands once the field is valid again',
        (tester) async {
      final budgets = await open(tester);

      await tester.enterText(find.byKey(_amountKey), '');
      await settleAutosave(tester);
      expect(budgets.updateCalls, isEmpty);

      await tester.enterText(find.byKey(_amountKey), '31');
      await settleAutosave(tester);

      expect(budgets.updateCalls, hasLength(1));
      expect(budgets.updateCalls.single['amount'], 31.0);
      expect(find.text(kExpenseAmountInvalidError), findsNothing);
    });

    testWidgets('dismissing with an invalid amount writes nothing at all',
        (tester) async {
      final budgets = await open(tester);

      await tester.enterText(find.byKey(_amountKey), '0');
      await tester.pump(const Duration(milliseconds: 50));
      await dismissWithoutAdvancingTime(tester);

      expect(budgets.updateCalls, isEmpty);
    });
  });

  group('null-vs-absent link contract', () {
    testWidgets('an unrelated edit carries the EXISTING task + sub-task link '
        'forward, still flagged as explicitly set', (tester) async {
      const linked = Expense(
        id: 'exp-91',
        projectId: 'proj-1',
        taskId: 't1',
        subtaskId: 's1',
        amount: 5,
        currency: 'USD',
        description: 'Flour',
        status: 'posted',
      );
      final budgets = await open(
        tester,
        expense: linked,
        tasks: [
          makeLinkedTask('t1', 'Bake a cake',
              category: 'Marketing', steps: _stepsJson),
        ],
      );

      await tester.enterText(find.byKey(_amountKey), '9');
      await settleAutosave(tester);

      final call = budgets.updateCalls.single;
      expect(call['amount'], 9.0);
      expect(call['taskId'], 't1');
      expect(call['taskIdSet'], isTrue);
      expect(call['subtaskId'], 's1');
      expect(call['subtaskIdSet'], isTrue);
    });

    testWidgets('a GHOST sub-task id is never resubmitted by an auto-save',
        (tester) async {
      const withGhost = Expense(
        id: 'exp-94',
        projectId: 'proj-1',
        taskId: 't1',
        subtaskId: 'ghost-subtask',
        amount: 5,
        currency: 'USD',
        description: 'Flour',
        status: 'posted',
      );
      final budgets = await open(
        tester,
        expense: withGhost,
        tasks: [
          makeLinkedTask('t1', 'Bake a cake',
              category: 'Marketing', steps: _stepsJson),
        ],
      );

      await tester.enterText(find.byKey(_amountKey), '9');
      await settleAutosave(tester);

      final call = budgets.updateCalls.single;
      expect(call['amount'], 9.0);
      expect(call['subtaskId'], isNull);
      expect(call['subtaskIdSet'], isTrue);
    });
  });

  group('destructive actions', () {
    testWidgets('Delete still needs an explicit confirm and never autosaves',
        (tester) async {
      final budgets = await open(tester);

      await tester.enterText(find.byKey(_descKey), 'About to delete');
      await settleAutosave(tester);
      expect(budgets.updateCalls, hasLength(1));

      await tester.ensureVisible(find.byKey(const Key('expense-detail-delete')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('expense-detail-delete')));
      await tester.pumpAndSettle();
      expect(budgets.deleteCalls, isEmpty, reason: 'confirm dialog is up');

      await tester.tap(find.text('Delete'));
      await tester.pumpAndSettle();

      expect(budgets.deleteCalls, ['exp-42']);
      expect(budgets.updateCalls, hasLength(1));
    });
  });

  group('the quiet saved indicator', () {
    testWidgets('nothing on an untouched sheet, Saved after a write',
        (tester) async {
      await open(tester);
      expect(find.text(kAutosaveSavedLabel), findsNothing);

      await tester.enterText(find.byKey(_descKey), 'Indicated');
      await settleAutosave(tester);

      expect(find.text(kAutosaveSavedLabel), findsOneWidget);
    });
  });
}
