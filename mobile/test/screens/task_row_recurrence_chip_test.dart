// Widget tests for the TaskRow recurrence chip.
//
// A task whose `recurring` cron parses to a known kind renders a subtle
// "🔁 <label>" chip; a custom cron renders a generic "Repeats" chip; a task
// with no recurrence renders no chip.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_row.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

Task _task({required String id, String? recurring, String? recurUntil}) =>
    Task(
      id: id,
      userId: 'u1',
      title: 'Sample task',
      priority: 'medium',
      status: 'todo',
      owner: 'user',
      dueDate: '2026-06-08',
      recurring: recurring,
      recurUntil: recurUntil,
      nagCount: 0,
      createdAt: '2026-06-06T00:00:00Z',
    );

Widget _host(Task task) => MaterialApp(
  theme: buildAppTheme(),
  home: Scaffold(
    body: TaskRow(
      task: task,
      pendingSync: false,
      onComplete: () {},
      onDelete: () {},
    ),
  ),
);

void main() {
  testWidgets('a weekly cron renders the recurrence chip with its label', (
    tester,
  ) async {
    await tester.pumpWidget(_host(_task(id: 'r1', recurring: '0 9 * * 1')));
    await tester.pump();
    expect(
      find.byKey(const ValueKey('task-row-recurrence-r1')),
      findsOneWidget,
    );
    expect(find.text('Weekly (Mon)'), findsOneWidget);
  });

  testWidgets('a daily cron renders "Daily"', (tester) async {
    await tester.pumpWidget(_host(_task(id: 'r2', recurring: '0 9 * * *')));
    await tester.pump();
    expect(find.text('Daily'), findsOneWidget);
  });

  testWidgets('a custom cron renders the generic "Repeats" chip', (
    tester,
  ) async {
    await tester.pumpWidget(_host(_task(id: 'r3', recurring: '*/15 * * * *')));
    await tester.pump();
    expect(
      find.byKey(const ValueKey('task-row-recurrence-r3')),
      findsOneWidget,
    );
    expect(find.text('Repeats'), findsOneWidget);
  });

  testWidgets('no recurrence → no chip', (tester) async {
    await tester.pumpWidget(_host(_task(id: 'r4', recurring: null)));
    await tester.pump();
    expect(find.byKey(const ValueKey('task-row-recurrence-r4')), findsNothing);
  });

  testWidgets('a series end appends "· until <day>" to the chip label', (
    tester,
  ) async {
    await tester.pumpWidget(_host(
      _task(id: 'r5', recurring: '0 9 * * *', recurUntil: '2026-09-30'),
    ));
    await tester.pump();
    expect(
      find.byKey(const ValueKey('task-row-recurrence-r5')),
      findsOneWidget,
    );
    expect(find.text('Daily · until 2026-09-30'), findsOneWidget);
  });

  testWidgets('an empty recurUntil (the cleared sentinel) keeps the plain '
      'label', (tester) async {
    await tester.pumpWidget(_host(
      _task(id: 'r6', recurring: '0 9 * * *', recurUntil: ''),
    ));
    await tester.pump();
    expect(find.text('Daily'), findsOneWidget);
  });
}
