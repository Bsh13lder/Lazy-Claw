// Widget tests for the TaskRow time-of-day tag.
//
// A task whose dueDate carries a time (a `T`-bearing ISO datetime) renders a
// separate time tag chip (e.g. `5:00 PM`) next to the date chip; a date-only
// task shows just the date with no time tag.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_row.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

Task _task({required String id, String? dueDate}) => Task(
      id: id,
      userId: 'u1',
      title: 'Sample task',
      priority: 'medium',
      status: 'todo',
      owner: 'user',
      dueDate: dueDate,
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
  testWidgets('a timed dueDate renders the time tag with the formatted time',
      (tester) async {
    await tester.pumpWidget(_host(_task(id: 't1', dueDate: '2026-06-08T17:00:00')));
    await tester.pump();

    // The time tag is present and shows a 12-hour label.
    expect(find.byKey(const ValueKey('task-row-time-t1')), findsOneWidget);
    expect(find.text('5:00 PM'), findsOneWidget);
    // The date chip shows only the date, not the raw datetime.
    expect(find.text('2026-06-08'), findsOneWidget);
    expect(find.text('2026-06-08T17:00:00'), findsNothing);
  });

  testWidgets('a date-only dueDate shows no time tag', (tester) async {
    await tester.pumpWidget(_host(_task(id: 't2', dueDate: '2026-06-08')));
    await tester.pump();

    expect(find.byKey(const ValueKey('task-row-time-t2')), findsNothing);
    expect(find.text('2026-06-08'), findsOneWidget);
  });

  testWidgets('a morning time formats with AM', (tester) async {
    await tester.pumpWidget(_host(_task(id: 't3', dueDate: '2026-06-08T09:30:00')));
    await tester.pump();

    expect(find.byKey(const ValueKey('task-row-time-t3')), findsOneWidget);
    expect(find.text('9:30 AM'), findsOneWidget);
  });
}
