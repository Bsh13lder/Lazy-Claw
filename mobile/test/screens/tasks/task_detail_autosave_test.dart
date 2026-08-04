// Auto-save behaviour of the TASK detail (edit) sheet.
//
// The expensive failure this guards is not "an edit was lost" — it is the
// opposite: a sheet that writes when the user changed nothing. Local writes go
// to the encrypted cache + the sync outbox and bump `updated_at`, and sync is
// last-write-wins, so one spurious write can clobber a real edit made
// elsewhere. Hence the very first test here.
//
// Matching is by KEY throughout: `Icons.attach_money_rounded` and
// `Icons.chat_bubble_outline` are each used by two different controls in this
// sheet, so `find.byIcon` counts lie.

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/autosave.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_budget_control.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_detail_patch.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_tags_field.dart';
import 'package:lazyclaw_mobile/widgets/autosave_indicator.dart';

import 'task_detail_harness.dart';

const _titleKey = Key('task-detail-title');
const _notesKey = Key('task-detail-notes');
const _saveKey = Key('task-detail-save');

Task _task({
  String title = 'Original title',
  String? category,
  String? tags,
  String? steps,
  double? allocatedBudget,
  String priority = 'medium',
  String? dueDate,
  String? reminderAt,
}) => Task(
  id: 't-1',
  userId: 'u1',
  title: title,
  description: 'Original notes',
  category: category,
  priority: priority,
  status: 'todo',
  owner: 'user',
  tags: tags,
  steps: steps,
  dueDate: dueDate,
  reminderAt: reminderAt,
  allocatedBudget: allocatedBudget,
  nagCount: 0,
  createdAt: '2026-08-01T00:00:00Z',
);

void main() {
  Future<StubTasksNotifier> open(
    WidgetTester tester, {
    Task? task,
  }) async {
    await tester.binding.setSurfaceSize(const Size(600, 1600));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final tasks = makeTasksStub(task ?? _task());
    await tester.pumpWidget(
      detailSheetHost(
        tasks: tasks,
        budgets: makeBudgetsStub(),
        task: task ?? _task(),
      ),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    return tasks;
  }

  /// Advance past the text debounce and let the write settle.
  ///
  /// `pumpAndSettle` alone is NOT enough: it only advances while frames are
  /// scheduled, and a bare `Timer` schedules none — so a debounced save would
  /// look like it never happened.
  Future<void> settleAutosave(WidgetTester tester) async {
    await tester.pump(kAutosaveDebounce + const Duration(milliseconds: 50));
    await tester.pumpAndSettle();
  }

  /// Unmount everything WITHOUT advancing the clock — so a still-pending
  /// debounce provably has not fired and any write observed afterwards can
  /// only have come from the dismiss-time flush.
  Future<void> dismissWithoutAdvancingTime(WidgetTester tester) async {
    await tester.pumpWidget(const SizedBox());
    await tester.pump();
  }

  group('no edits', () {
    testWidgets('opening and closing writes NOTHING — no update, no outbox '
        'churn, no updated_at bump', (tester) async {
      final tasks = await open(tester);

      // Dismiss via the barrier, exactly as a user does.
      await tester.tapAt(const Offset(10, 10));
      await tester.pumpAndSettle();

      expect(tasks.updateCalls, isEmpty);
    });

    testWidgets('tapping Save with no edits also writes nothing', (tester) async {
      final tasks = await open(tester);

      await tester.tap(find.byKey(_saveKey));
      await tester.pumpAndSettle();

      expect(tasks.updateCalls, isEmpty);
      expect(find.byKey(_titleKey), findsNothing, reason: 'the sheet closed');
    });

    testWidgets('a caret move in a text field is not an edit', (tester) async {
      final tasks = await open(tester);

      final field = tester.widget<TextField>(find.byKey(_titleKey));
      field.controller!.selection = const TextSelection.collapsed(offset: 3);
      await tester.pumpAndSettle();

      expect(tasks.updateCalls, isEmpty);
    });
  });

  group('debounced text', () {
    testWidgets('several keystrokes produce exactly ONE write', (tester) async {
      final tasks = await open(tester);

      for (final text in ['O', 'Or', 'Ord', 'Order coffee']) {
        await tester.enterText(find.byKey(_titleKey), text);
        await tester.pump(const Duration(milliseconds: 80));
      }
      expect(tasks.updateCalls, isEmpty, reason: 'still inside the debounce');

      await tester.pump(kAutosaveDebounce + const Duration(milliseconds: 50));
      await tester.pumpAndSettle();

      expect(tasks.updateCalls, hasLength(1));
      expect(tasks.updateCalls.single['title'], 'Order coffee');
    });

    testWidgets('editing notes autosaves too', (tester) async {
      final tasks = await open(tester);

      // Non-empty notes open as a read-only preview; tap to get the field.
      await tester.tap(find.byKey(const Key('task-detail-notes-preview')));
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(_notesKey), 'Revised notes');
      await settleAutosave(tester);

      expect(tasks.updateCalls, hasLength(1));
      expect(tasks.updateCalls.single['description'], 'Revised notes');
    });

    testWidgets('a second identical edit does not write again', (tester) async {
      final tasks = await open(tester);

      await tester.enterText(find.byKey(_titleKey), 'Renamed');
      await settleAutosave(tester);
      expect(tasks.updateCalls, hasLength(1));

      // Type it back to exactly what was just saved.
      await tester.enterText(find.byKey(_titleKey), 'Renamed x');
      await settleAutosave(tester);
      await tester.enterText(find.byKey(_titleKey), 'Renamed');
      await settleAutosave(tester);

      expect(
        tasks.updateCalls.map((c) => c['title']),
        ['Renamed', 'Renamed x', 'Renamed'],
        reason: 'each distinct value is a real change; no extra no-op writes',
      );
    });
  });

  group('discrete controls', () {
    testWidgets('a priority chip commits immediately, without waiting out the '
        'debounce', (tester) async {
      final tasks = await open(tester);

      await tester.tap(find.text('high'));
      await tester.pump();

      expect(tasks.updateCalls, hasLength(1));
      expect(tasks.updateCalls.single['priority'], 'high');
    });

    testWidgets('a due-date chip commits immediately', (tester) async {
      final tasks = await open(tester);

      await tester.tap(find.text('Today'));
      await tester.pump();

      expect(tasks.updateCalls, hasLength(1));
      expect(tasks.updateCalls.single['dueDate'], isNotEmpty);
    });
  });

  group('dismiss', () {
    testWidgets('a pending debounce is FLUSHED on dismiss, not dropped',
        (tester) async {
      final tasks = await open(tester);

      await tester.enterText(find.byKey(_titleKey), 'Typed then swiped away');
      await tester.pump(const Duration(milliseconds: 50));
      expect(tasks.updateCalls, isEmpty, reason: 'debounce still running');

      await dismissWithoutAdvancingTime(tester);

      expect(tasks.updateCalls, hasLength(1));
      expect(tasks.updateCalls.single['title'], 'Typed then swiped away');
    });

    testWidgets('dismissing an untouched sheet still writes nothing',
        (tester) async {
      final tasks = await open(tester);
      await dismissWithoutAdvancingTime(tester);
      expect(tasks.updateCalls, isEmpty);
    });
  });

  group('coalescing', () {
    testWidgets('edits landing during an in-flight save collapse into ONE '
        'follow-up carrying the final state', (tester) async {
      final tasks = await open(tester);
      final gate = Completer<void>();
      tasks.updateGate = gate;

      await tester.tap(find.text('high'));
      await tester.pump();
      expect(tasks.updateCalls, hasLength(1), reason: 'first save in flight');

      // Three more edits while the first write is parked.
      await tester.tap(find.text('low'));
      await tester.pump();
      await tester.enterText(find.byKey(_titleKey), 'Final');
      await tester.pump(kAutosaveDebounce + const Duration(milliseconds: 50));
      await tester.tap(find.text('urgent'));
      await tester.pump();
      expect(tasks.updateCalls, hasLength(1),
          reason: 'no second write may start while one is in flight');

      tasks.updateGate = null;
      gate.complete();
      await tester.pumpAndSettle();

      expect(tasks.updateCalls, hasLength(2),
          reason: 'three queued edits collapse to exactly one follow-up');
      expect(tasks.updateCalls.last['priority'], 'urgent');
      expect(tasks.updateCalls.last['title'], 'Final');
    });
  });

  group('invalid state', () {
    testWidgets('an empty title is never written over a good one — the write '
        'is held and the error is shown inline', (tester) async {
      final tasks = await open(tester);

      await tester.enterText(find.byKey(_titleKey), '');
      await settleAutosave(tester);

      expect(tasks.updateCalls, isEmpty);
      expect(find.text(kTaskTitleRequiredError), findsOneWidget);
    });

    testWidgets('the held edit is written once the title is valid again',
        (tester) async {
      final tasks = await open(tester);

      await tester.enterText(find.byKey(_titleKey), '');
      await settleAutosave(tester);
      expect(tasks.updateCalls, isEmpty);

      await tester.enterText(find.byKey(_titleKey), 'Recovered');
      await settleAutosave(tester);

      expect(tasks.updateCalls, hasLength(1));
      expect(tasks.updateCalls.single['title'], 'Recovered');
      expect(find.text(kTaskTitleRequiredError), findsNothing);
    });

    testWidgets('dismissing with an empty title writes nothing at all',
        (tester) async {
      final tasks = await open(tester);

      await tester.enterText(find.byKey(_titleKey), '');
      await tester.pump(const Duration(milliseconds: 50));
      await dismissWithoutAdvancingTime(tester);

      expect(tasks.updateCalls, isEmpty);
    });

    testWidgets('a junk allocation is refused rather than silently dropped',
        (tester) async {
      final tasks = await open(tester, task: _task(allocatedBudget: 100));

      await tester.tap(find.byKey(kTaskBudgetSummaryTapKey));
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(kTaskBudgetFieldKey), 'abc');
      await settleAutosave(tester);

      expect(tasks.updateCalls, isEmpty);
      expect(find.text(kTaskAllocationInvalidError), findsOneWidget);
    });
  });

  group('three-way patch contract', () {
    testWidgets('a title-only edit leaves every untouched column ABSENT from '
        'the patch', (tester) async {
      final tasks = await open(
        tester,
        task: _task(
          category: 'Marketing',
          tags: '["a"]',
          steps: '[{"id":"s1","title":"one","done":false}]',
          allocatedBudget: 40,
          // A DATE-ONLY due with a real reminder — the shape every respawned
          // recurring task has, and the one where sending the composed value
          // unconditionally used to delete the reminder on an unrelated edit.
          dueDate: '2026-09-01',
          reminderAt: '2026-08-31T09:00:00',
        ),
      );

      await tester.enterText(find.byKey(_titleKey), 'Just the title');
      await settleAutosave(tester);

      final call = tasks.updateCalls.single;
      expect(call['title'], 'Just the title');
      expect(call['category'], isNull, reason: 'untouched project');
      expect(call['steps'], isNull, reason: 'untouched checklist');
      expect(call['tags'], isNull, reason: 'untouched tags');
      expect(call['recurring'], isNull, reason: 'untouched repeat');
      expect(call['recurUntil'], isNull, reason: 'untouched series end');
      expect(call['reminderAt'], isNull, reason: 'untouched reminder');
      expect(call['allocatedBudget'], isNull);
      expect(call['clearAllocatedBudget'], isFalse);
    });

    testWidgets('clearing an AUTO-SAVED allocation still clears it — the '
        'three-way baseline must advance with every write, or the revert is '
        'read as "untouched" and the number stays in the database forever',
        (tester) async {
      final tasks = await open(tester, task: _task());

      // Task starts with NO allocation; type one and let it auto-save.
      await tester.tap(find.byKey(kTaskBudgetSummaryTapKey));
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(kTaskBudgetFieldKey), '300');
      await settleAutosave(tester);
      expect(tasks.updateCalls.single['allocatedBudget'], 300.0);

      // Now clear it.
      await tester.enterText(find.byKey(kTaskBudgetFieldKey), '');
      await settleAutosave(tester);

      expect(tasks.updateCalls, hasLength(2));
      expect(tasks.updateCalls.last['clearAllocatedBudget'], isTrue);
    });

    testWidgets('removing an AUTO-SAVED tag still clears it', (tester) async {
      final tasks = await open(tester, task: _task(tags: '["alpha"]'));

      await tester.tap(find.byKey(kTaskTagsChipKey));
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(kTaskTagInputKey), 'beta');
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(kTaskTagsDoneKey));
      await tester.pumpAndSettle();

      expect(tasks.updateCalls, hasLength(1));
      expect(tasks.updateCalls.single['tags'], '["alpha","beta"]');

      // Remove it again — the patch must carry the SHORTER list, which only
      // works if the baseline advanced to the auto-saved one.
      await tester.tap(find.byKey(kTaskTagsChipKey));
      await tester.pumpAndSettle();
      await tester.tap(find.text('beta'));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(kTaskTagsDoneKey));
      await tester.pumpAndSettle();

      expect(tasks.updateCalls.last['tags'], '["alpha"]');
    });
  });

  group('destructive actions', () {
    testWidgets('Delete still needs an explicit confirm — it never autosaves',
        (tester) async {
      final tasks = await open(tester);

      await tester.enterText(find.byKey(_titleKey), 'About to delete');
      await settleAutosave(tester);
      expect(tasks.updateCalls, hasLength(1));

      await tester.tap(find.byKey(const Key('task-detail-delete')));
      await tester.pumpAndSettle();
      expect(tasks.deleteCalls, isEmpty, reason: 'confirm dialog is up');

      await tester.tap(find.text('Delete').last);
      await tester.pumpAndSettle();

      expect(tasks.deleteCalls, ['t-1']);
      expect(tasks.updateCalls, hasLength(1),
          reason: 'no field write may follow the delete');
    });
  });

  group('the quiet saved indicator', () {
    testWidgets('shows nothing on an untouched sheet, then Saved after a write',
        (tester) async {
      final tasks = await open(tester);

      expect(find.text(kAutosaveSavedLabel), findsNothing);

      await tester.enterText(find.byKey(_titleKey), 'Indicated');
      await settleAutosave(tester);

      expect(tasks.updateCalls, hasLength(1));
      expect(find.text(kAutosaveSavedLabel), findsOneWidget);
    });

    testWidgets('reads "Not saved" while the title is blank', (tester) async {
      await open(tester);

      await tester.enterText(find.byKey(_titleKey), '');
      await settleAutosave(tester);

      expect(find.text(kAutosaveBlockedLabel), findsOneWidget);
    });
  });
}
