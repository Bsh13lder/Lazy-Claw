import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/chat_socket.dart';
import 'package:lazyclaw_mobile/chat/ws_frames.dart';

void main() {
  test('emits parsed frames from the underlying socket', () async {
    final incoming = StreamController<dynamic>();
    final sent = <String>[];
    final socket = ChatSocket(
      channelFactory: (url, headers) => FakeSink(incoming.stream, sent),
    );
    final frames = <ServerFrame>[];
    socket.frames.listen(frames.add);
    await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc');

    incoming.add('{"type":"token","content":"hi"}');
    incoming.add('{"type":"done","content":"hi there"}');
    await Future<void>.delayed(Duration.zero);

    expect(frames.whereType<TokenFrame>().length, 1);
    expect(frames.whereType<DoneFrame>().length, 1);

    socket.send('hello');
    expect(sent.last, contains('"type":"message"'));
    await incoming.close();
  });
}
