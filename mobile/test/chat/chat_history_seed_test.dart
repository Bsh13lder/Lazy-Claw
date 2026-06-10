import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/chat_controller.dart';
import 'package:lazyclaw_mobile/chat/chat_message.dart';
import 'package:lazyclaw_mobile/chat/chat_socket.dart';

void main() {
  group('ChatReducer.seedHistory', () {
    test('no-op for an empty history list', () {
      final r = ChatReducer();
      r.seedHistory(const []);
      expect(r.messages, isEmpty);
    });

    test('seeds into an empty reducer in order', () {
      final r = ChatReducer();
      r.seedHistory(const [
        ChatMessage(role: 'user', content: 'a'),
        ChatMessage(role: 'assistant', content: 'b'),
      ]);
      expect(r.messages.map((m) => m.content), ['a', 'b']);
    });

    test('inserts history ABOVE live messages already streamed', () {
      final r = ChatReducer();
      // A live turn arrived before history finished loading.
      r.onUserSend('live question');
      r.seedHistory(const [
        ChatMessage(role: 'user', content: 'old q'),
        ChatMessage(role: 'assistant', content: 'old a'),
      ]);
      // History sits first, live turn stays last.
      expect(r.messages.first.content, 'old q');
      expect(r.messages[1].content, 'old a');
      expect(r.messages[2].content, 'live question');
    });
  });

  group('ChatController.seedHistory', () {
    // A bare ChatSocket is idle until connect() is called, so no real IO
    // happens — only its `frames` broadcast stream is subscribed.
    test('is idempotent — only the first call seeds', () {
      final socket = ChatSocket();
      final c = ChatController(socket);
      c.seedHistory(const [ChatMessage(role: 'user', content: 'one')]);
      c.seedHistory(const [ChatMessage(role: 'user', content: 'two')]);
      expect(c.state, hasLength(1));
      expect(c.state.first.content, 'one');
      c.dispose();
      socket.dispose();
    });

    test('an empty first seed still marks seeded (no later double-seed)', () {
      final socket = ChatSocket();
      final c = ChatController(socket);
      c.seedHistory(const []);
      c.seedHistory(const [ChatMessage(role: 'user', content: 'late')]);
      expect(c.state, isEmpty);
      c.dispose();
      socket.dispose();
    });
  });
}
