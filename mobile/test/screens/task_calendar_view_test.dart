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

/// A finder matching any [_TaskDot] marker rendered for a ghost occurrence —
/// keyed `ghost-marker-<taskId>` in `_DayMarkers.build` — anywhere in the
/// tree. Deliberately day-agnostic: it counts markers across the WHOLE
/// rendered month grid, not just the selected day. TableCalendar builds every
/// visible day cell at once, so a task whose cron matches several days in
/// range renders several markers (one per day, correctly) — tests using this
/// finder to assert "exactly one marker" must pin the fixture cron (e.g. a
/// yearly cron) so it matches only a single day within the visible range,
/// or the count reflects matched-days, not "ghosts on one day".
final Finder _ghostMarkerFinder = find.byWidgetPredicate(
  (w) => w.key is ValueKey<String> &&
      (w.key! as ValueKey<String>).value.startsWith('ghost-marker-'),
);

Widget _calendar({
  required List<Task> tasks,
  required DateTime focusedDay,
  required DateTime selectedDay,
  ValueChanged<String>? onComplete,
  ValueChanged<String>? onDelete,
  // Pins "now" for the recurrence-ghost clamp (expandRecurringForRange never
  // ghosts before this day) so a ghost-on-a-hardcoded-day assertion doesn't
  // silently start failing once the real wall clock catches up to that day.
  DateTime? now,
  bool showRepeats = true,
  ValueChanged<bool>? onShowRepeatsChanged,
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
      ghostsNow: now,
      showRepeats: showRepeats,
      onShowRepeatsChanged: onShowRepeatsChanged,
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
          // Pinned so the ghost-clamp (never project before "now") never
          // strips Aug 10 once the real calendar date passes it.
          now: DateTime(2026, 8, 3),
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
          now: DateTime(2026, 8, 3),
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
          now: DateTime(2026, 8, 3),
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

    testWidgets(
      'a ghost day before the injected "now" is clamped away — proves the '
      'past-clamp reaches the widget, not just the pure function',
      (tester) async {
        // Same weekly-Monday cron as above, but the selected day (Jul 6) is
        // BEFORE the injected "now" (Aug 3) — the ghost must not render.
        final tasks = [
          _task('a', recurring: '0 9 * * 1', title: 'Weekly standup'),
        ];

        await tester.pumpWidget(_host(_calendar(
          tasks: tasks,
          focusedDay: DateTime(2026, 8, 1),
          selectedDay: DateTime(2026, 7, 6), // a Monday, but in the "past"
          now: DateTime(2026, 8, 3),
        )));
        await tester.pumpAndSettle();

        expect(find.text('Nothing due this day'), findsOneWidget);
        expect(find.byIcon(Icons.repeat_rounded), findsNothing);
      },
    );
  });

  // Regression coverage for the 2026-08 "every day says ○ ○ ○ +37" report:
  // ~37 recurring tasks all ghosting on the same day used to inflate the
  // "+N" overflow badge with the ghost count and render one ring PER ghost.
  // Ghosts must never contribute to overflow, and at most one ghost marker
  // may ever render per day. `'0 9 15 8 *'` (yearly, Aug 15) is used so every
  // ghost-producing task lands on exactly ONE day within the widget's ~3
  // month visible range — keeping the marker-count assertion unambiguous.
  group('ghost overflow regression (2026-08)', () {
    testWidgets(
      '2 real tasks + 40 recurring tasks all landing on the same day '
      'renders both real dots, exactly ONE ghost marker, and no "+40" '
      'overflow badge',
      (tester) async {
        final tasks = [
          _task('r1', dueDate: '2026-08-15', title: 'Real one'),
          _task('r2', dueDate: '2026-08-15', title: 'Real two'),
          for (var i = 0; i < 40; i++)
            _task('g$i', recurring: '0 9 15 8 *', title: 'Recurring $i'),
        ];

        await tester.pumpWidget(_host(_calendar(
          tasks: tasks,
          focusedDay: DateTime(2026, 8, 1),
          selectedDay: DateTime(2026, 8, 15),
          now: DateTime(2026, 8, 1),
        )));
        await tester.pumpAndSettle();

        expect(_ghostMarkerFinder, findsOneWidget);
        expect(find.textContaining('+40'), findsNothing);
        // 2 real tasks fit within maxDots(3) with room for the one ghost
        // slot left over, so there is no overflow badge for this day at all.
        expect(find.textContaining('+'), findsNothing);
      },
    );

    testWidgets(
      'maxDots (3) real tasks already fill every dot slot: no ghost marker '
      'renders even though 40 recurring tasks ghost the same day, and '
      'overflow reflects only the real tasks',
      (tester) async {
        final tasks = [
          _task('r1', dueDate: '2026-08-15', title: 'Real one'),
          _task('r2', dueDate: '2026-08-15', title: 'Real two'),
          _task('r3', dueDate: '2026-08-15', title: 'Real three'),
          _task('r4', dueDate: '2026-08-15', title: 'Real four'),
          for (var i = 0; i < 40; i++)
            _task('g$i', recurring: '0 9 15 8 *', title: 'Recurring $i'),
        ];

        await tester.pumpWidget(_host(_calendar(
          tasks: tasks,
          focusedDay: DateTime(2026, 8, 1),
          selectedDay: DateTime(2026, 8, 15),
          now: DateTime(2026, 8, 1),
        )));
        await tester.pumpAndSettle();

        expect(_ghostMarkerFinder, findsNothing);
        // 4 real tasks, maxDots 3 shown → real overflow is 1, never 41.
        expect(find.textContaining('+1'), findsOneWidget);
        expect(find.textContaining('+40'), findsNothing);
        expect(find.textContaining('+41'), findsNothing);
      },
    );
  });

  group('Show repeats toggle', () {
    testWidgets(
      'OFF: no ghost marker and no ghost row anywhere, even for a day that '
      'would otherwise be a pure-ghost day',
      (tester) async {
        final tasks = [
          _task('a', recurring: '0 9 * * 1', title: 'Weekly standup'),
        ];

        await tester.pumpWidget(_host(_calendar(
          tasks: tasks,
          focusedDay: DateTime(2026, 8, 1),
          selectedDay: DateTime(2026, 8, 10), // pure-ghost Monday
          now: DateTime(2026, 8, 3),
          showRepeats: false,
        )));
        await tester.pumpAndSettle();

        expect(find.text('Weekly standup'), findsNothing);
        expect(_ghostMarkerFinder, findsNothing);
        expect(find.text('Nothing due this day'), findsOneWidget);
      },
    );

    testWidgets(
      'ON (default): the ghost marker and ghost row both return',
      (tester) async {
        // Yearly (not weekly) so the cron matches exactly one day within the
        // widget's ~3-month visible range — a weekly cron here would ghost
        // on every Monday in range, and _ghostMarkerFinder (deliberately
        // day-agnostic — see its doc) would then find one marker per
        // matching day instead of the single one this test means to assert.
        final tasks = [
          _task('a', recurring: '0 9 15 8 *', title: 'Weekly standup'),
        ];

        await tester.pumpWidget(_host(_calendar(
          tasks: tasks,
          focusedDay: DateTime(2026, 8, 1),
          selectedDay: DateTime(2026, 8, 15),
          now: DateTime(2026, 8, 1),
        )));
        await tester.pumpAndSettle();

        expect(find.text('Weekly standup'), findsOneWidget);
        expect(_ghostMarkerFinder, findsOneWidget);
      },
    );

    testWidgets(
      'the toggle affordance is absent when onShowRepeatsChanged is null '
      '(the default — mirrors TasksProjectView.onHideCompletedChanged)',
      (tester) async {
        await tester.pumpWidget(_host(_calendar(
          tasks: const [],
          focusedDay: DateTime(2026, 8, 1),
          selectedDay: DateTime(2026, 8, 1),
        )));
        await tester.pumpAndSettle();

        expect(
          find.byKey(const ValueKey('calendar-show-repeats-toggle')),
          findsNothing,
        );
      },
    );

    testWidgets(
      'the toggle is present when wired, swaps its icon glyph by state, and '
      'fires onShowRepeatsChanged with the flipped value on tap',
      (tester) async {
        bool? toggled;
        await tester.pumpWidget(_host(_calendar(
          tasks: const [],
          focusedDay: DateTime(2026, 8, 1),
          selectedDay: DateTime(2026, 8, 1),
          showRepeats: true,
          onShowRepeatsChanged: (v) => toggled = v,
        )));
        await tester.pumpAndSettle();

        expect(
          find.byKey(const ValueKey('calendar-show-repeats-toggle')),
          findsOneWidget,
        );
        expect(find.byIcon(Icons.repeat_on_rounded), findsOneWidget);

        await tester.tap(
          find.byKey(const ValueKey('calendar-show-repeats-toggle')),
        );
        await tester.pumpAndSettle();

        expect(toggled, isFalse);
      },
    );
  });
}
