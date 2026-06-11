// ActivityTimelineRow: live tool segment while running, capped tool-name
// summary when settled, and the 3-minute stall guard that swaps the spinner
// for a muted schedule icon when a running row stops receiving updates.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:lazyclaw_mobile/chat/chat_message.dart';
import 'package:lazyclaw_mobile/screens/chat/activity_timeline.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

Future<void> _pump(WidgetTester tester, AgentActivity a) async {
  await tester.pumpWidget(MaterialApp(
    theme: buildAppTheme(),
    home: Scaffold(body: ActivityTimeline(activities: [a])),
  ));
}

void main() {
  testWidgets('running row with currentTool shows the live tool segment',
      (tester) async {
    await _pump(
      tester,
      const AgentActivity(
        kind: 'specialist',
        subject: 'research',
        detail: 'using web_search',
        toolsUsed: ['web_search'],
        currentTool: 'web_search',
      ),
    );
    expect(find.textContaining('using web_search…', findRichText: true),
        findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
  });

  testWidgets('settled row caps the tool list at 5 names plus overflow',
      (tester) async {
    await _pump(
      tester,
      const AgentActivity(
        kind: 'specialist',
        subject: 'research',
        detail: 'finished',
        done: true,
        toolsUsed: ['t1', 't2', 't3', 't4', 't5', 't6', 't7'],
      ),
    );
    expect(find.textContaining('t1, t2, t3, t4, t5 +2', findRichText: true),
        findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);
  });

  testWidgets('settled row with few tools lists all names, no overflow',
      (tester) async {
    await _pump(
      tester,
      const AgentActivity(
        kind: 'bg',
        subject: 'check whatsapp',
        detail: 'finished',
        done: true,
        toolsUsed: ['whatsapp_read', 'find_contact'],
      ),
    );
    expect(
        find.textContaining('whatsapp_read, find_contact',
            findRichText: true),
        findsOneWidget);
    expect(find.textContaining('+', findRichText: true), findsNothing);
  });

  testWidgets('stall guard swaps the spinner for a schedule icon after 3 min',
      (tester) async {
    await _pump(
      tester,
      const AgentActivity(kind: 'bg', subject: 'long job', detail: 'started'),
    );
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    expect(find.byIcon(Icons.schedule), findsNothing);

    await tester.pump(const Duration(minutes: 3, seconds: 1));

    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(find.byIcon(Icons.schedule), findsOneWidget);
  });

  testWidgets('an activity update resets the stall timer', (tester) async {
    await _pump(
      tester,
      const AgentActivity(kind: 'bg', subject: 'long job', detail: 'started'),
    );
    // 2 minutes in, fresh content arrives — the clock must restart.
    await tester.pump(const Duration(minutes: 2));
    await _pump(
      tester,
      const AgentActivity(
        kind: 'bg',
        subject: 'long job',
        detail: 'still working',
        events: ['started', 'still working'],
      ),
    );
    await tester.pump(const Duration(minutes: 2));
    expect(find.byType(CircularProgressIndicator), findsOneWidget,
        reason: 'only 2 min since the last update — not stalled');

    await tester.pump(const Duration(minutes: 1, seconds: 1));
    expect(find.byIcon(Icons.schedule), findsOneWidget);
  });

  testWidgets('a settled row never shows the stall icon', (tester) async {
    await _pump(
      tester,
      const AgentActivity(
        kind: 'bg',
        subject: 'done job',
        detail: 'finished',
        done: true,
      ),
    );
    await tester.pump(const Duration(minutes: 4));
    expect(find.byIcon(Icons.schedule), findsNothing);
    expect(find.byIcon(Icons.check_circle_outline), findsOneWidget);
  });
}
