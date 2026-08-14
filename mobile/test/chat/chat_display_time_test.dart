import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/chat_message.dart';
import 'package:lazyclaw_mobile/repositories/chat_history_repository.dart';
import 'package:lazyclaw_mobile/screens/chat/chat_bubble.dart';

void main() {
  group('displayContent strips internal reasoning blocks', () {
    test('plan block removed from assistant text', () {
      const m = ChatMessage(
        role: 'assistant',
        content: '<plan>\n<goal>x</goal>\n</plan>\nReal answer.',
      );
      expect(m.displayContent, 'Real answer.');
    });

    test('dangling open tag scrubbed', () {
      const m = ChatMessage(role: 'assistant', content: '<plan>\ncut off');
      expect(m.displayContent.contains('<plan>'), isFalse);
    });

    test('user content never touched', () {
      const m = ChatMessage(role: 'user', content: '<plan> literal from me');
      expect(m.displayContent, '<plan> literal from me');
    });

    test('clean assistant text untouched', () {
      const m = ChatMessage(role: 'assistant', content: 'a < b and `code`');
      expect(m.displayContent, 'a < b and `code`');
    });
  });

  group('parseServerTime', () {
    test('sqlite space format parsed as UTC', () {
      final dt = parseServerTime('2026-08-13 15:31:09');
      expect(dt, DateTime.utc(2026, 8, 13, 15, 31, 9));
    });

    test('iso with offset preserved', () {
      final dt = parseServerTime('2026-08-13T15:31:09+02:00');
      expect(dt, DateTime.utc(2026, 8, 13, 13, 31, 9));
    });

    test('garbage yields null', () {
      expect(parseServerTime('not a date'), isNull);
      expect(parseServerTime(''), isNull);
      expect(parseServerTime(null), isNull);
    });
  });

  group('formatBubbleTime', () {
    test('today shows HH:mm only', () {
      final now = DateTime.now();
      final utc =
          DateTime(now.year, now.month, now.day, 14, 5).toUtc();
      expect(formatBubbleTime(utc), '14:05');
    });

    test('other day shows date and time', () {
      final label = formatBubbleTime(DateTime.utc(2026, 1, 15, 10, 0));
      expect(label.contains('Jan'), isTrue);
      expect(label.contains('·'), isTrue);
    });
  });
}
