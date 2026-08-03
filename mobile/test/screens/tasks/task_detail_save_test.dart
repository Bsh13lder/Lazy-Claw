// D5 — Save is the reusable floating square submit (always over the sheet's
// viewport, never pushed off the end of a grown form), and Delete is moved
// deliberately far from it.
//
// The old footer put a full-width Save immediately beside a `Delete Task`
// button at the very bottom of a thumb-driven sheet. That is one mis-tap away
// from destroying a task, so this file asserts the separation as a contract,
// not as a coincidence of layout.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import 'task_detail_harness.dart';

const _task = Task(
  id: 'task-7',
  userId: 'u1',
  title: 'Original title',
  priority: 'medium',
  status: 'todo',
  owner: 'user',
  nagCount: 0,
  createdAt: '2026-06-06T00:00:00Z',
);

const _saveKey = Key('task-detail-save');
const _deleteKey = Key('task-detail-delete');

void main() {
  Future<StubTasksNotifier> open(WidgetTester tester) async {
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(900, 2400);
    addTearDown(tester.view.reset);

    final tasks = makeTasksStub(_task);
    await tester.pumpWidget(
      detailSheetHost(tasks: tasks, budgets: makeBudgetsStub(), task: _task),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    return tasks;
  }

  testWidgets('Save is the kit floating submit, not a footer button', (
    tester,
  ) async {
    await open(tester);

    expect(find.byKey(_saveKey), findsOneWidget);
    expect(tester.widget(find.byKey(_saveKey)), isA<LzFloatingSubmit>());
    // The old full-width footer button is gone.
    expect(find.widgetWithText(LzButton, 'Save'), findsNothing);
    expect(find.text('Delete Task'), findsNothing);
  });

  testWidgets('tapping the floating save writes through updateTask', (
    tester,
  ) async {
    final stub = await open(tester);

    await tester.enterText(
      find.byKey(const Key('task-detail-title')),
      'Edited',
    );
    await tester.tap(find.byKey(_saveKey));
    await tester.pumpAndSettle();

    expect(stub.updateCalls, hasLength(1));
    expect(stub.updateCalls.single['title'], 'Edited');
    // Saving closes the sheet.
    expect(find.byKey(const Key('task-detail-title')), findsNothing);
  });

  testWidgets(
    'Delete is nowhere near Save — a mis-tap on a thumb-driven sheet must '
    'not be able to destroy the task',
    (tester) async {
      await open(tester);

      final save = tester.getRect(find.byKey(_saveKey));
      final del = tester.getRect(find.byKey(_deleteKey));

      expect(del.overlaps(save), isFalse);
      // Delete lives at the TOP of the sheet, Save at the bottom-right.
      expect(del.center.dy, lessThan(save.center.dy));
      expect(
        (save.center - del.center).distance,
        greaterThan(LzFloatingSubmit.size * 2),
      );
    },
  );

  testWidgets('Delete still confirms first, then deletes', (tester) async {
    final stub = await open(tester);

    await tester.tap(find.byKey(_deleteKey));
    await tester.pumpAndSettle();

    // Confirmation is up; nothing destroyed yet.
    expect(stub.deleteCalls, isEmpty);
    expect(find.text('Delete task?'), findsOneWidget);

    await tester.tap(find.text('Delete'));
    await tester.pumpAndSettle();

    expect(stub.deleteCalls, ['task-7']);
    expect(find.byKey(const Key('task-detail-title')), findsNothing);
  });

  testWidgets('cancelling the confirm leaves the task alone', (tester) async {
    final stub = await open(tester);

    await tester.tap(find.byKey(_deleteKey));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();

    expect(stub.deleteCalls, isEmpty);
    expect(find.byKey(const Key('task-detail-title')), findsOneWidget);
  });

  testWidgets(
    'the save button stays on screen on a SHORT viewport, where a footer '
    'button would have been scrolled out of reach',
    (tester) async {
      tester.view.devicePixelRatio = 1.0;
      tester.view.physicalSize = const Size(400, 700);
      addTearDown(tester.view.reset);

      await tester.pumpWidget(
        detailSheetHost(
          tasks: makeTasksStub(_task),
          budgets: makeBudgetsStub(),
          task: _task,
        ),
      );
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();

      final rect = tester.getRect(find.byKey(_saveKey));
      expect(rect.top, greaterThanOrEqualTo(0));
      expect(rect.bottom, lessThanOrEqualTo(700));
      expect(find.byKey(_saveKey), findsOneWidget);
    },
  );
}
