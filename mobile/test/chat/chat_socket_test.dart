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
    await socket.dispose();
  });

  test('automatically reconnects after the socket drops', () async {
    // Each connect attempt gets its own controller so we can drop them
    // independently and observe the reconnect re-open a fresh channel.
    final controllers = <StreamController<dynamic>>[];
    var factoryCalls = 0;
    final socket = ChatSocket(
      baseBackoff: const Duration(milliseconds: 5),
      maxBackoff: const Duration(milliseconds: 20),
      channelFactory: (url, headers) {
        factoryCalls++;
        final c = StreamController<dynamic>();
        controllers.add(c);
        return FakeSink(c.stream, <String>[]);
      },
    );
    final states = <bool>[];
    socket.connectionState.listen(states.add);

    await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc');
    expect(factoryCalls, 1);

    // Simulate the server/connection dropping.
    await controllers.first.close();

    // Give the backoff timer time to fire and re-open a fresh channel.
    await Future<void>.delayed(const Duration(milliseconds: 60));

    expect(factoryCalls, greaterThanOrEqualTo(2),
        reason: 'socket should re-open after a drop');
    // Saw a disconnect (false) followed by a recovery (true).
    expect(states.contains(false), isTrue);
    expect(states.last, isTrue, reason: 'should end up reconnected');

    await socket.dispose();
  });

  test('stops reconnecting after dispose', () async {
    final controllers = <StreamController<dynamic>>[];
    var factoryCalls = 0;
    final socket = ChatSocket(
      baseBackoff: const Duration(milliseconds: 5),
      maxBackoff: const Duration(milliseconds: 20),
      channelFactory: (url, headers) {
        factoryCalls++;
        final c = StreamController<dynamic>();
        controllers.add(c);
        return FakeSink(c.stream, <String>[]);
      },
    );
    await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc');
    expect(factoryCalls, 1);

    await socket.dispose();
    await controllers.first.close();
    await Future<void>.delayed(const Duration(milliseconds: 60));

    expect(factoryCalls, 1,
        reason: 'no reconnect attempts should fire after dispose');
  });
}
