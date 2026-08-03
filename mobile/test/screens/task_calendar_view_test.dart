// Widget tests for TaskCalendarView's visual fixes (Task 3 — calendar
// correctness, P2):
//   * 3b. a recurring task's projected ghost occurrences render on the
//     selected-day list as a clearly-non-real row (no complete/delete
//     wiring to the wrong task), and never duplicate the real materialised
//     occurrence on its own day.
//   * 3c. a fully-done day's marker is a solid, opaque success dot — not the
//     near-invisible 18%-alpha ring it used to be (diagnosis D3).
//
// `TaskCalendarView` is a plain StatelessWidget (tasks/projects/callbacks in,
// no provider/DB dependency), so this pumps it directly with plain callback
// stubs — no riverpod overrides, no fake Database (house style: widget tests
// use plain callbacks/fakes).

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_calendar_view.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_row.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

Task _task(
  String id, {
  String? dueDate,
  String? recurring,
  String status = 'todo',
  String title = 'Task',
}) =>
    Task(
      id: id,
      userId: 'u1',
      title: title,
      priority: 'medium',
      status: status,
      owner: 'user',
      dueDate: dueDate,
      recurring: recurring,
      nagCount: 0,
      createdAt: '2026-06-06T00:00:00Z',
    );

Widget _host(Widget child) =>
    MaterialApp(theme: buildAppTheme(), home: Scaffold(body: child));

Widget _calendar({
  required List<Task> tasks,
  required DateTime focusedDay,
  required DateTime selectedDay,
  ValueChanged<String>? onComplete,
  ValueChanged<String>? onDelete,
}) =>
    TaskCalendarView(
      tasks: tasks,
      projects: const <Project>[],
      dirtyIds: const {},
      focusedDay: focusedDay,
      selectedDay: selectedDay,
      onDaySelected: (_, _) {},
      onPageChanged: (_) {},
      onComplete: onComplete ?? (_) {},
      onDelete: onDelete ?? (_) {},
      onOpen: (_) {},
      onAddOnDay: (_) {},
    );

void main() {
  group('3c — all-done badge visibility', () {
    testWidgets(
      'a fully-done day renders a solid, opaque AppColors.success marker '
      '(not an 18%-alpha ring)',
      (tester) async {
        final tasks = [
          _task('a', dueDate: '2026-08-05', status: 'done', title: 'Done task'),
        ];

        await tester.pumpWidget(_host(_calendar(
          tasks: tasks,
          focusedDay: DateTime(2026, 8, 1),
          selectedDay: DateTime(2026, 8, 5),
        )));
        await tester.pumpAndSettle();

        final solidSuccessDots = tester
            .widgetList<Container>(find.byType(Container))
            .where((c) {
          final decoration = c.decoration;
          if (decoration is! BoxDecoration) return false;
          // Fully opaque AppColors.success — the old badge used
          // `.withValues(alpha: 0.18)`, a different (translucent) color.
          return decoration.color == AppColors.success;
        });

        expect(solidSuccessDots, isNotEmpty);
      },
    );
  });

  group('3b — recurrence ghosts', () {
    testWidgets(
      'a ghost occurrence day (no real task) shows a repeat-styled row, not '
      'the empty state, and does not render a real TaskRow',
      (tester) async {
        // 2026-08-03 is a Monday; '0 9 * * 1' ghosts every Monday. The task
        // has no dueDate at all, so 2026-08-10 (also a Monday) is a
        // pure-ghost day — nothing real is due there.
        final tasks = [
          _task('a', recurring: '0 9 * * 1', title: 'Weekly standup'),
        ];

        await tester.pumpWidget(_host(_calendar(
          tasks: tasks,
          focusedDay: DateTime(2026, 8, 1),
          selectedDay: DateTime(2026, 8, 10),
        )));
        await tester.pumpAndSettle();

        expect(find.text('Nothing due this day'), findsNothing);
        expect(find.text('Weekly standup'), findsOneWidget);
        // A ghost must never render as a real, actionable TaskRow (that
        // would wire complete/delete to the wrong semantic — the task's own
        // id, on a day it isn't actually due).
        expect(find.byType(TaskRow), findsNothing);
        expect(find.byIcon(Icons.repeat_rounded), findsOneWidget);
      },
    );

    testWidgets(
      'the real materialised occurrence on its own due day is NOT '
      'duplicated as a ghost',
      (tester) async {
        final tasks = [
          _task(
            'a',
            dueDate: '2026-08-03', // Monday — matches the cron below.
            recurring: '0 9 * * 1',
            title: 'Weekly standup',
          ),
        ];

        await tester.pumpWidget(_host(_calendar(
          tasks: tasks,
          focusedDay: DateTime(2026, 8, 1),
          selectedDay: DateTime(2026, 8, 3),
        )));
        await tester.pumpAndSettle();

        // Exactly one row for this task on its real day — the real TaskRow,
        // no extra ghost row alongside it.
        expect(find.text('Weekly standup'), findsOneWidget);
        expect(find.byType(TaskRow), findsOneWidget);
        expect(find.byIcon(Icons.repeat_rounded), findsNothing);
      },
    );

    testWidgets(
      'ghosts never contribute complete/delete callbacks',
      (tester) async {
        var completeCalls = 0;
        var deleteCalls = 0;
        final tasks = [
          _task('a', recurring: '0 9 * * 1', title: 'Weekly standup'),
        ];

        await tester.pumpWidget(_host(_calendar(
          tasks: tasks,
          focusedDay: DateTime(2026, 8, 1),
          selectedDay: DateTime(2026, 8, 10), // pure-ghost day
          onComplete: (_) => completeCalls++,
          onDelete: (_) => deleteCalls++,
        )));
        await tester.pumpAndSettle();

        // No TaskRow (the only widget wired to onComplete/onDelete) is
        // present for the ghost, so there is nothing to tap that could fire
        // either callback.
        expect(find.byType(TaskRow), findsNothing);
        expect(completeCalls, 0);
        expect(deleteCalls, 0);
      },
    );
  });
}
