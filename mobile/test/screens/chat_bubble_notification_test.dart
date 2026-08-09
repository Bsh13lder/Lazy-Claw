// Notification-kind assistant rows ("{title}\n{body}") render inside a
// standard assistant bubble with a bell glyph + emphasized title line;
// rows without the kind field render as normal assistant bubbles.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:lazyclaw_mobile/chat/chat_message.dart';
import 'package:lazyclaw_mobile/screens/chat/chat_bubble.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

Future<void> _pump(WidgetTester tester, ChatMessage m) async {
  await tester.pumpWidget(MaterialApp(
    theme: buildAppTheme(),
    home: Scaffold(body: ChatBubble(m, onApprove: (_, _) {})),
  ));
}

void main() {
  testWidgets('notification row shows the bell glyph, title and body',
      (tester) async {
    await _pump(
      tester,
      const ChatMessage(
        role: 'assistant',
        content: 'Task due\nPay the invoice today',
        id: 'n1',
        kind: 'notification',
      ),
    );
    expect(find.byIcon(Icons.notifications_none_rounded), findsOneWidget);
    expect(find.text('Task due'), findsOneWidget);
    expect(find.textContaining('Pay the invoice today'), findsOneWidget);
  });

  testWidgets('single-line notification renders title-only (no crash)',
      (tester) async {
    await _pump(
      tester,
      const ChatMessage(
        role: 'assistant',
        content: 'Watcher fired',
        kind: 'notification',
      ),
    );
    expect(find.byIcon(Icons.notifications_none_rounded), findsOneWidget);
    expect(find.text('Watcher fired'), findsOneWidget);
  });

  testWidgets('assistant row WITHOUT kind renders as a normal bubble',
      (tester) async {
    await _pump(
      tester,
      const ChatMessage(role: 'assistant', content: 'plain reply'),
    );
    expect(find.byIcon(Icons.notifications_none_rounded), findsNothing);
    expect(find.textContaining('plain reply'), findsOneWidget);
  });

  testWidgets('user bubbles never get the notification treatment',
      (tester) async {
    await _pump(
      tester,
      const ChatMessage(
        role: 'user',
        content: 'Task due\nnot a ping',
        kind: 'notification',
      ),
    );
    expect(find.byIcon(Icons.notifications_none_rounded), findsNothing);
  });
}
