import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/chat_socket.dart';
import 'package:lazyclaw_mobile/assistant/cloud_turn_client.dart';

void main() {
  test('streamTurn yields tokens then completes on DoneFrame', () async {
    final ctrl = StreamController<dynamic>();
    final sent = <String>[];
    final socket = ChatSocket(channelFactory: (_, __) => FakeSink(ctrl.stream, sent));
    await socket.connect('ws://x', cookie: 'session_id=abc');
    final client = CloudTurnClient(socket);

    final out = <String>[];
    final done = client.streamTurn('hi').forEach(out.add);

    await Future<void>.delayed(Duration.zero);
    ctrl.add('{"type":"token","content":"Hel"}');
    ctrl.add('{"type":"token","content":"lo"}');
    ctrl.add('{"type":"done","content":"Hello"}');
    await done;

    expect(out, ['Hel', 'lo']);
    expect(sent.single, contains('"content":"hi"'));
  });

  test('streamTurn yields done.content when the server streams NO tokens', () async {
    // Reproduces the "AI replied but nothing shows on Flutter" bug: on
    // quiet-mode / non-streaming turns (the tool/channel/action turns the
    // router hard-escalates to cloud), chat_ws.py forwards NO `token` frames
    // and delivers the whole reply in the terminal `done` payload
    // (`{"type":"done","content": result or cb._buffer}`). The client must
    // surface that content — exactly as the main-chat reducer does.
    final ctrl = StreamController<dynamic>();
    final socket = ChatSocket(channelFactory: (_, __) => FakeSink(ctrl.stream, []));
    await socket.connect('ws://x', cookie: 'session_id=abc');
    final client = CloudTurnClient(socket);

    final out = <String>[];
    final done = client.streamTurn('check my whatsapp').forEach(out.add);

    await Future<void>.delayed(Duration.zero);
    ctrl.add('{"type":"done","content":"You have 3 unread WhatsApp messages."}');
    await done;

    expect(out, ['You have 3 unread WhatsApp messages.']);
  });

  test('streamTurn does NOT double-emit when tokens already streamed', () async {
    // When tokens DID stream live, the terminal done payload repeats the same
    // full text — the client must not append it a second time.
    final ctrl = StreamController<dynamic>();
    final socket = ChatSocket(channelFactory: (_, __) => FakeSink(ctrl.stream, []));
    await socket.connect('ws://x', cookie: 'session_id=abc');
    final client = CloudTurnClient(socket);

    final out = <String>[];
    final done = client.streamTurn('hi').forEach(out.add);

    await Future<void>.delayed(Duration.zero);
    ctrl.add('{"type":"token","content":"Hel"}');
    ctrl.add('{"type":"token","content":"lo"}');
    ctrl.add('{"type":"done","content":"Hello"}');
    await done;

    expect(out.join(), 'Hello');
  });

  test('streamTurn throws CloudTurnException on ErrorFrame', () async {
    final ctrl = StreamController<dynamic>();
    final socket = ChatSocket(channelFactory: (_, __) => FakeSink(ctrl.stream, []));
    await socket.connect('ws://x', cookie: 'session_id=abc');
    final client = CloudTurnClient(socket);

    final fut = client.streamTurn('hi').toList();
    await Future<void>.delayed(Duration.zero);
    ctrl.add('{"type":"error","message":"boom"}');
    await expectLater(fut, throwsA(isA<CloudTurnException>()));
  });
}
