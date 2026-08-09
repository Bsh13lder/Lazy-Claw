// Cron-kind user rows ([JOB:...] / [WATCHER:...] / [REMINDER...] instruction
// text persisted as a user message) render as a compact centered system pill
// instead of a genuine right-aligned user bubble; tapping expands the full
// instruction inline. Rows without the kind field keep the normal bubble
// (server not yet deployed = no regression).
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:lazyclaw_mobile/chat/chat_message.dart';
import 'package:lazyclaw_mobile/screens/chat/chat_bubble.dart';
import 'package:lazyclaw_mobile/screens/chat/cron_row.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

Future<void> _pump(WidgetTester tester, ChatMessage m) async {
  await tester.pumpWidget(MaterialApp(
    theme: buildAppTheme(),
    home: Scaffold(body: ChatBubble(m, onApprove: (_, _) {})),
  ));
}

void main() {
  group('cronJobLabel', () {
    test('extracts the job name from a [JOB:name] prefix', () {
      expect(cronJobLabel('[JOB:daily-briefing] Run the briefing'),
          'daily-briefing');
    });

    test('extracts the watcher name from a [WATCHER:name] prefix', () {
      expect(cronJobLabel('[WATCHER:upwork-inbox] Poll the inbox'),
          'upwork-inbox');
    });

    test('reminder prefixes fall back to a readable label', () {
      expect(cronJobLabel('[REMINDER] Pay the invoice'), 'Reminder');
      expect(cronJobLabel('[REMINDER: invoice] Pay it'), 'invoice');
    });

    test('unparseable content falls back to Scheduled job', () {
      expect(cronJobLabel('no prefix at all'), 'Scheduled job');
      expect(cronJobLabel('[JOB:unclosed'), 'Scheduled job');
    });
  });

  group('CronSystemRow rendering', () {
    testWidgets('cron user row renders a centered pill, not a user bubble',
        (tester) async {
      await _pump(
        tester,
        const ChatMessage(
          role: 'user',
          content: '[JOB:daily-briefing] Run the morning briefing now',
          kind: 'cron',
        ),
      );
      expect(find.byType(CronSystemRow), findsOneWidget);
      expect(find.byIcon(Icons.schedule), findsOneWidget);
      expect(find.text('daily-briefing'), findsOneWidget);
      // The full instruction stays hidden until tapped.
      expect(
          find.textContaining('Run the morning briefing now'), findsNothing);
    });

    testWidgets('tapping the pill expands the full instruction inline',
        (tester) async {
      await _pump(
        tester,
        const ChatMessage(
          role: 'user',
          content: '[WATCHER:upwork-inbox] Poll the inbox every 15 minutes',
          kind: 'cron',
        ),
      );
      await tester.tap(find.byType(CronSystemRow));
      await tester.pump();
      expect(find.textContaining('Poll the inbox every 15 minutes'),
          findsOneWidget);

      // Tapping again collapses it.
      await tester.tap(find.text('upwork-inbox'));
      await tester.pump();
      expect(find.textContaining('Poll the inbox every 15 minutes'),
          findsNothing);
    });

    testWidgets('user rows WITHOUT the kind field keep the normal bubble',
        (tester) async {
      await _pump(
        tester,
        const ChatMessage(
          role: 'user',
          content: '[JOB:looks-like-cron] but no kind field',
        ),
      );
      expect(find.byType(CronSystemRow), findsNothing);
      expect(find.textContaining('[JOB:looks-like-cron]'), findsOneWidget);
    });

    testWidgets('assistant rows never get the cron treatment', (tester) async {
      await _pump(
        tester,
        const ChatMessage(
          role: 'assistant',
          content: '[JOB:x] echoing the prefix',
          kind: 'cron',
        ),
      );
      expect(find.byType(CronSystemRow), findsNothing);
    });
  });
}
