import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/ws_frames.dart';

void main() {
  test('parses channel_message frame', () {
    final f = parseServerFrame('{"type":"channel_message","thread_id":"t1",'
        '"sender_name":"Alice","content":"hi","timestamp":"10:00"}');
    expect(f, isA<ChannelMessageFrame>());
    final c = f as ChannelMessageFrame;
    expect(c.threadId, 't1');
    expect(c.senderName, 'Alice');
  });

  test('channel_message frame uses empty strings for missing fields', () {
    final f = parseServerFrame('{"type":"channel_message"}');
    expect(f, isA<ChannelMessageFrame>());
    final c = f as ChannelMessageFrame;
    expect(c.threadId, '');
    expect(c.senderName, '');
    expect(c.content, '');
    expect(c.timestamp, '');
  });

  test('channel_message frame carries all fields', () {
    final f = parseServerFrame(
      '{"type":"channel_message","thread_id":"wa:123",'
      '"sender_name":"Bob","content":"hello there","timestamp":"14:30"}',
    );
    expect(f, isA<ChannelMessageFrame>());
    final c = f as ChannelMessageFrame;
    expect(c.threadId, 'wa:123');
    expect(c.senderName, 'Bob');
    expect(c.content, 'hello there');
    expect(c.timestamp, '14:30');
  });
}
