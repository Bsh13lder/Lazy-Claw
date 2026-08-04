// The shared "created / done" line, mounted on its own.
//
// The rule that matters most here is the NEGATIVE one: a sub-task that
// predates the timestamp fields has neither value and never will, so the line
// must render literally nothing — no "null", no 1970 date, no em-dash
// placeholder that reads as a bug.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_timestamps.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

/// Every [Text] the line itself put on screen (scoped so the host Scaffold's
/// own internals can never make an "renders nothing" assertion vacuous).
final _textsInside = find.descendant(
  of: find.byType(TaskTimestampsLine),
  matching: find.byType(Text),
);

void main() {
  final now = DateTime.utc(2026, 8, 4, 12);

  Widget host({String? createdAt, String? completedAt}) => MaterialApp(
    theme: buildAppTheme(),
    home: Scaffold(
      body: TaskTimestampsLine(
        keyPrefix: 'x',
        createdAt: createdAt,
        completedAt: completedAt,
        now: now,
      ),
    ),
  );

  testWidgets('renders the created label', (tester) async {
    await tester.pumpWidget(host(createdAt: '2026-08-04T09:00:00Z'));
    await tester.pump();

    expect(find.byKey(const Key('x-created')), findsOneWidget);
    expect(find.text('Created 3h ago'), findsOneWidget);
    expect(find.byKey(const Key('x-completed')), findsNothing);
  });

  testWidgets('renders both labels once there is a completion time', (
    tester,
  ) async {
    await tester.pumpWidget(
      host(
        createdAt: '2026-08-04T09:00:00Z',
        completedAt: '2026-08-04T11:55:00Z',
      ),
    );
    await tester.pump();

    expect(find.text('Created 3h ago'), findsOneWidget);
    // The separator rides INSIDE the second label so a Wrap that breaks to a
    // second line can't orphan a lone middot.
    expect(find.text('· Done 5m ago'), findsOneWidget);
  });

  testWidgets('a completion time with no creation time stands alone '
      '(no leading separator)', (tester) async {
    // Reachable today: TaskDao's parent-completion cascade ticks legacy
    // sub-tasks that never had a `created_at`.
    await tester.pumpWidget(host(completedAt: '2026-08-04T11:55:00Z'));
    await tester.pump();

    expect(find.text('Done 5m ago'), findsOneWidget);
    expect(find.byKey(const Key('x-created')), findsNothing);
  });

  testWidgets('a legacy row with no timestamps renders NOTHING', (
    tester,
  ) async {
    await tester.pumpWidget(host());
    await tester.pump();

    expect(find.byKey(const Key('x-created')), findsNothing);
    expect(find.byKey(const Key('x-completed')), findsNothing);
    expect(_textsInside, findsNothing);
    // Not even the padding survives — an empty line would still push the
    // rows below it down by a hairline on every legacy checklist.
    expect(
      find.descendant(
        of: find.byType(TaskTimestampsLine),
        matching: find.byType(Padding),
      ),
      findsNothing,
    );
  });

  testWidgets('garbage timestamps degrade to nothing instead of crashing', (
    tester,
  ) async {
    await tester.pumpWidget(host(createdAt: 'nope', completedAt: ''));
    await tester.pump();

    expect(_textsInside, findsNothing);
  });
}
