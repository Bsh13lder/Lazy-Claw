// User-bubble send-state hints: "Sending…" while queued in the socket
// outbox, "Not delivered" once the per-message TTL expires.
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
  testWidgets('queued user bubble shows the Sending… hint', (tester) async {
    await _pump(
      tester,
      const ChatMessage(
        role: 'user',
        content: 'hello there',
        sendState: SendState.sending,
      ),
    );
    expect(find.text('hello there'), findsOneWidget);
    expect(find.text('Sending…'), findsOneWidget);
  });

  testWidgets('failed user bubble shows the Not delivered hint',
      (tester) async {
    await _pump(
      tester,
      const ChatMessage(
        role: 'user',
        content: 'lost one',
        sendState: SendState.failed,
      ),
    );
    expect(find.text('lost one'), findsOneWidget);
    expect(find.text('Not delivered'), findsOneWidget);
  });

  testWidgets('a normally sent user bubble shows no hint', (tester) async {
    await _pump(
      tester,
      const ChatMessage(role: 'user', content: 'plain'),
    );
    expect(find.text('Sending…'), findsNothing);
    expect(find.text('Not delivered'), findsNothing);
  });
}
