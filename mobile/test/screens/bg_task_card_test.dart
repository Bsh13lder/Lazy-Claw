// BgTaskCard: full card for distinct background results; header-only
// (name + status icon + duration, no body) when the result was flagged as a
// duplicate of a recent assistant reply by the reducer.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:lazyclaw_mobile/chat/chat_message.dart';
import 'package:lazyclaw_mobile/screens/chat/bg_task_card.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

Future<void> _pump(WidgetTester tester, BackgroundTaskResult r) async {
  await tester.pumpWidget(MaterialApp(
    theme: buildAppTheme(),
    home: Scaffold(body: BgTaskCard(r)),
  ));
}

void main() {
  const detail = 'Daily briefing: 3 tasks due today, 2 overdue. '
      'Focus on the eStreet bot delivery.';

  testWidgets('normal card shows the name, body and duration', (tester) async {
    await _pump(
      tester,
      const BackgroundTaskResult(
        name: 'Task Guardian',
        success: true,
        detail: detail,
        durationMs: 1200,
        events: ['started', 'finished'],
      ),
    );
    expect(find.text('Task Guardian'), findsOneWidget);
    expect(find.text(detail), findsOneWidget);
    expect(find.text('1.2s'), findsOneWidget);
    expect(find.byIcon(Icons.check_circle_outline), findsOneWidget);
  });

  testWidgets('duplicate-flagged card renders header-only — no body text',
      (tester) async {
    await _pump(
      tester,
      const BackgroundTaskResult(
        name: 'Task Guardian',
        success: true,
        detail: detail,
        durationMs: 1200,
        events: ['started', 'finished'],
        duplicateOfReply: true,
      ),
    );
    // Header survives: name + ✅ + time.
    expect(find.text('Task Guardian'), findsOneWidget);
    expect(find.byIcon(Icons.check_circle_outline), findsOneWidget);
    expect(find.text('1.2s'), findsOneWidget);
    // Body is gone: no result text, no activity log toggle.
    expect(find.text(detail), findsNothing);
    expect(find.textContaining('Activity ('), findsNothing);
  });
}
