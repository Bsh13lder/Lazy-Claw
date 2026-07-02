// Widget tests for the add-task sheet's notes + sub-tasks wiring:
//   * a Notes field and a Sub-tasks editor are present in the sheet,
//   * typing notes + adding a sub-task then submitting carries both out on the
//     result record (description + serialized steps), so addTask can persist
//     them on create (the fields were previously dropped).
//
// Uses the SAME safe host pattern as the recurrence/reminder tests: the real
// showAddTaskSheet helper inside a ProviderScope, with NO live tasks notifier —
// the sheet only returns a record, which the host captures.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/subtask.dart';
import 'package:lazyclaw_mobile/screens/tasks/add_task_sheet.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

void main() {
  ({String? description, String? steps})? captured;

  Widget host() => ProviderScope(
    child: MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        body: Center(
          child: Builder(
            builder: (ctx) => ElevatedButton(
              onPressed: () async {
                final r = await showAddTaskSheet(ctx);
                captured = r == null
                    ? null
                    : (description: r.description, steps: r.steps);
              },
              child: const Text('open'),
            ),
          ),
        ),
      ),
    ),
  );

  setUp(() => captured = null);

  testWidgets('the sheet shows a Notes field and a Sub-tasks editor', (
    tester,
  ) async {
    await tester.pumpWidget(host());
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('add-task-notes')), findsOneWidget);
    expect(find.text('SUBTASKS'), findsOneWidget);
    expect(find.byKey(const Key('subtask-add-field')), findsOneWidget);
  });

  testWidgets('notes + a sub-task ride the result on submit', (tester) async {
    await tester.pumpWidget(host());
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).first, 'Plan trip');
    await tester.pump();

    // Notes → description.
    await tester.enterText(
      find.byKey(const Key('add-task-notes')),
      'Book flights and hotel',
    );
    await tester.pump();

    // Add one sub-task via the trailing "add a sub-task" field.
    await tester.enterText(
      find.byKey(const Key('subtask-add-field')),
      'Buy tickets',
    );
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pump();

    await tester.ensureVisible(find.text('Add Task'));
    await tester.pump();
    await tester.tap(find.text('Add Task'));
    await tester.pumpAndSettle();

    expect(captured, isNotNull);
    expect(captured?.description, 'Book flights and hotel');
    final parsed = parseSubtasks(captured?.steps);
    expect(parsed, hasLength(1));
    expect(parsed.single.title, 'Buy tickets');
    expect(parsed.single.done, isFalse);
  });

  testWidgets('no notes + no sub-tasks → null description + null steps', (
    tester,
  ) async {
    await tester.pumpWidget(host());
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).first, 'Just a title');
    await tester.pump();

    await tester.ensureVisible(find.text('Add Task'));
    await tester.pump();
    await tester.tap(find.text('Add Task'));
    await tester.pumpAndSettle();

    expect(captured, isNotNull);
    expect(captured?.description, isNull);
    expect(captured?.steps, isNull);
  });
}
