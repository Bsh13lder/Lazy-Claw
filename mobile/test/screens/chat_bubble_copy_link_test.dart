import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/chat_message.dart';
import 'package:lazyclaw_mobile/screens/chat/chat_bubble.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

Widget _host(ChatMessage m) {
  return MaterialApp(
    theme: buildAppTheme(),
    home: Scaffold(
      body: SingleChildScrollView(
        child: ChatBubble(m, onApprove: (_, __) {}),
      ),
    ),
  );
}

void main() {
  testWidgets('user bubble text is selectable (copyable)', (tester) async {
    await tester.pumpWidget(_host(
      const ChatMessage(role: 'user', content: 'copy me please'),
    ));
    expect(
      find.byWidgetPredicate(
        (w) => w is SelectableText && w.data == 'copy me please',
      ),
      findsOneWidget,
    );
  });

  testWidgets('settled assistant markdown is selectable with link handler',
      (tester) async {
    await tester.pumpWidget(_host(
      const ChatMessage(
        role: 'assistant',
        content: 'see [the site](https://example.com) now',
      ),
    ));
    final md = tester.widget<MarkdownBody>(find.byType(MarkdownBody));
    expect(md.selectable, isTrue);
    expect(md.onTapLink, isNotNull);
  });

  testWidgets('streaming assistant bubble is NOT selectable (selection would '
      'reset every token repaint)', (tester) async {
    await tester.pumpWidget(_host(
      const ChatMessage(
        role: 'assistant',
        content: 'partial tok',
        streaming: true,
      ),
    ));
    final md = tester.widget<MarkdownBody>(find.byType(MarkdownBody));
    expect(md.selectable, isFalse);
  });

  test('openChatLink ignores empty, schemeless, and unsafe hrefs', () async {
    // Must complete without throwing — launchUrl is never reached.
    await openChatLink('t', null, '');
    await openChatLink('t', '   ', '');
    await openChatLink('t', 'example.com/no-scheme', '');
    await openChatLink('t', 'javascript:alert(1)', '');
    await openChatLink('t', 'file:///etc/passwd', '');
  });
}
