// Pending-outbox behavior: a send while the socket is disconnected must
// never be silently dropped. It queues (bounded FIFO), flushes on reconnect,
// and expires with a visible failure after the per-message TTL.
import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/chat_controller.dart';
import 'package:lazyclaw_mobile/chat/chat_message.dart';
import 'package:lazyclaw_mobile/chat/chat_socket.dart';
import 'package:lazyclaw_mobile/chat/ws_frames.dart';

void main() {
  // ── ChatSocket: queue + flush + TTL ──────────────────────────────────────

  test('send returns true and writes immediately when connected', () async {
    final incoming = StreamController<dynamic>();
    final sent = <String>[];
    final socket = ChatSocket(
      channelFactory: (url, headers) => FakeSink(incoming.stream, sent),
    );
    await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc');

    expect(socket.send('hello'), isTrue);
    expect(sent.last, contains('"content":"hello"'));

    await incoming.close();
    await socket.dispose();
  });

  test('send before connect queues, returns false, and flushes on connect',
      () async {
    final incoming = StreamController<dynamic>();
    final sent = <String>[];
    final socket = ChatSocket(
      channelFactory: (url, headers) => FakeSink(incoming.stream, sent),
    );
    final flushes = <int>[];
    socket.outboxFlushed.listen(flushes.add);

    expect(socket.send('queued one'), isFalse);
    expect(socket.send('queued two'), isFalse);
    expect(sent, isEmpty, reason: 'nothing written while disconnected');

    await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc');
    await Future<void>.delayed(Duration.zero);

    // FIFO order preserved.
    expect(sent.length, 2);
    expect(sent[0], contains('"content":"queued one"'));
    expect(sent[1], contains('"content":"queued two"'));
    expect(flushes, [2]);

    await incoming.close();
    await socket.dispose();
  });

  test('send during a mid-backoff drop queues and flushes on reconnect',
      () async {
    final controllers = <StreamController<dynamic>>[];
    final sinkSent = <List<String>>[];
    final socket = ChatSocket(
      baseBackoff: const Duration(milliseconds: 10),
      maxBackoff: const Duration(milliseconds: 20),
      channelFactory: (url, headers) {
        final c = StreamController<dynamic>();
        controllers.add(c);
        final sent = <String>[];
        sinkSent.add(sent);
        return FakeSink(c.stream, sent);
      },
    );
    final flushes = <int>[];
    socket.outboxFlushed.listen(flushes.add);

    await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc');
    // Drop the connection; send while the backoff timer is pending.
    await controllers.first.close();
    expect(socket.send('while offline'), isFalse);
    expect(sinkSent.first, isEmpty, reason: 'dead sink must not receive it');

    // Let the backoff fire and re-open a fresh channel.
    await Future<void>.delayed(const Duration(milliseconds: 60));

    expect(sinkSent.length, greaterThanOrEqualTo(2));
    expect(sinkSent[1].single, contains('"content":"while offline"'));
    expect(flushes, [1]);

    await socket.dispose();
  });

  test('queued message expires after TTL with a SendFailedFrame', () async {
    final incoming = StreamController<dynamic>();
    final sent = <String>[];
    final socket = ChatSocket(
      outboxTtl: const Duration(milliseconds: 30),
      channelFactory: (url, headers) => FakeSink(incoming.stream, sent),
    );
    final frames = <ServerFrame>[];
    socket.frames.listen(frames.add);
    final flushes = <int>[];
    socket.outboxFlushed.listen(flushes.add);

    expect(socket.send('doomed'), isFalse);
    await Future<void>.delayed(const Duration(milliseconds: 80));

    expect(frames.whereType<SendFailedFrame>().length, 1,
        reason: 'expiry must surface a visible failure frame');

    // A later connect must NOT deliver the expired message.
    await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc');
    await Future<void>.delayed(Duration.zero);
    expect(sent, isEmpty);
    expect(flushes, isEmpty);

    await incoming.close();
    await socket.dispose();
  });

  test('outbox is bounded: oldest is evicted with a SendFailedFrame',
      () async {
    final incoming = StreamController<dynamic>();
    final sent = <String>[];
    final socket = ChatSocket(
      channelFactory: (url, headers) => FakeSink(incoming.stream, sent),
    );
    final frames = <ServerFrame>[];
    socket.frames.listen(frames.add);

    for (var i = 0; i < 21; i++) {
      socket.send('msg $i');
    }
    await Future<void>.delayed(Duration.zero);
    expect(frames.whereType<SendFailedFrame>().length, 1);

    await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc');
    await Future<void>.delayed(Duration.zero);

    expect(sent.length, 20);
    expect(sent.first, contains('"content":"msg 1"'),
        reason: 'msg 0 was evicted; FIFO from msg 1');
    expect(sent.last, contains('"content":"msg 20"'));

    await incoming.close();
    await socket.dispose();
  });

  test('expiry timers do not fire after dispose', () async {
    final socket = ChatSocket(
      outboxTtl: const Duration(milliseconds: 20),
      channelFactory: (url, headers) =>
          FakeSink(const Stream<dynamic>.empty(), <String>[]),
    );
    socket.send('never delivered');
    await socket.dispose();
    // The frames controller is closed — a late timer firing would throw.
    await Future<void>.delayed(const Duration(milliseconds: 60));
  });

  // ── ChatReducer: queued / delivered / failed user-bubble states ──────────

  test('queued send adds a sending user bubble and NO streaming spinner', () {
    final c = ChatReducer();
    c.onUserSend('offline text', delivered: false);
    expect(c.messages.length, 1);
    expect(c.messages.single.role, 'user');
    expect(c.messages.single.sendState, SendState.sending);
    expect(c.isStreaming, isFalse,
        reason: 'no assistant turn until the message actually went out');
  });

  test('delivered send keeps the existing two-message shape', () {
    final c = ChatReducer();
    c.onUserSend('hi');
    expect(c.messages.length, 2);
    expect(c.messages.first.sendState, SendState.sent);
    expect(c.messages.last.streaming, isTrue);
  });

  test('onOutboxFlushed marks sending bubbles sent and starts the spinner',
      () {
    final c = ChatReducer();
    c.onUserSend('offline text', delivered: false);
    c.onOutboxFlushed(1);
    expect(c.messages.length, 2);
    expect(c.messages.first.sendState, SendState.sent);
    expect(c.messages.last.role, 'assistant');
    expect(c.messages.last.streaming, isTrue);
    expect(c.isStreaming, isTrue);
  });

  test('onOutboxFlushed marks multiple queued bubbles, one spinner', () {
    final c = ChatReducer();
    c.onUserSend('one', delivered: false);
    c.onUserSend('two', delivered: false);
    c.onOutboxFlushed(2);
    final users = c.messages.where((m) => m.role == 'user').toList();
    expect(users.every((m) => m.sendState == SendState.sent), isTrue);
    expect(c.messages.where((m) => m.streaming).length, 1);
  });

  test(
      'onOutboxFlushed with a queued bubble after a streaming turn keeps '
      'one spinner', () {
    final c = ChatReducer();
    c.onUserSend('first'); // delivered — spinner up
    c.onUserSend('second', delivered: false); // queued user bubble is last
    c.onOutboxFlushed(1);
    expect(c.messages.where((m) => m.streaming).length, 1,
        reason: 'flush must not stack a second spinner mid-turn');
    final users = c.messages.where((m) => m.role == 'user');
    expect(users.every((m) => m.sendState == SendState.sent), isTrue);
  });

  test('onOutboxFlushed does not stack a second spinner when streaming', () {
    final c = ChatReducer();
    c.onUserSend('first'); // delivered — spinner up
    c.onOutboxFlushed(0);
    expect(c.messages.where((m) => m.streaming).length, 1);
  });

  test('SendFailedFrame fails the oldest sending bubble and adds an error',
      () {
    final c = ChatReducer();
    c.onUserSend('lost message', delivered: false);
    c.onFrame(const SendFailedFrame('Message not delivered — resend it.'));

    expect(c.messages.first.role, 'user');
    expect(c.messages.first.content, 'lost message',
        reason: 'the user text must never be clobbered');
    expect(c.messages.first.sendState, SendState.failed);
    expect(c.messages.last.role, 'assistant');
    expect(c.messages.last.content, contains('Message not delivered'));
    expect(c.messages.last.streaming, isFalse);
    expect(c.isStreaming, isFalse);
  });

  test('ErrorFrame while last bubble is a queued user message is a no-op', () {
    final c = ChatReducer();
    c.onUserSend('still queued', delivered: false);
    c.onFrame(const ErrorFrame('Disconnected'));
    expect(c.messages.length, 1,
        reason: 'a drop mid-queue must not fail the bubble — TTL owns that');
    expect(c.messages.single.content, 'still queued');
    expect(c.messages.single.sendState, SendState.sending);
  });

  test('ErrorFrame after a sent-but-bubble-less user message appends, '
      'never clobbers', () {
    final c = ChatReducer();
    c.onUserSend('queued then failed', delivered: false);
    c.onFrame(const SendFailedFrame('expired'));
    // Last is now the assistant error bubble; a fresh queued message follows.
    c.onUserSend('again', delivered: false);
    c.onOutboxFlushed(1); // delivered, spinner up
    c.onFrame(const ErrorFrame('server error'));
    expect(c.messages.last.content, contains('server error'));
    // The user texts are intact.
    expect(c.messages.where((m) => m.role == 'user').map((m) => m.content),
        ['queued then failed', 'again']);
  });

  // ── ChatController: end-to-end over a fake socket ────────────────────────

  test('controller send over a dropped socket queues, then flushes on '
      'reconnect', () async {
    final controllers = <StreamController<dynamic>>[];
    final sinkSent = <List<String>>[];
    final socket = ChatSocket(
      baseBackoff: const Duration(milliseconds: 10),
      maxBackoff: const Duration(milliseconds: 20),
      channelFactory: (url, headers) {
        final c = StreamController<dynamic>();
        controllers.add(c);
        final sent = <String>[];
        sinkSent.add(sent);
        return FakeSink(c.stream, sent);
      },
    );
    final controller = ChatController(socket);

    await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc');
    await controllers.first.close(); // drop

    controller.send('hello while down');
    expect(controller.state.length, 1);
    expect(controller.state.single.sendState, SendState.sending);
    expect(controller.isStreaming, isFalse);

    // Backoff fires, channel re-opens, outbox flushes.
    await Future<void>.delayed(const Duration(milliseconds: 80));

    expect(sinkSent[1].single, contains('"content":"hello while down"'));
    expect(controller.state.first.sendState, SendState.sent);
    expect(controller.state.last.streaming, isTrue);

    controller.dispose();
    await socket.dispose();
  });

  test('controller surfaces TTL expiry as a failed bubble + error message',
      () async {
    final socket = ChatSocket(
      outboxTtl: const Duration(milliseconds: 20),
      channelFactory: (url, headers) =>
          FakeSink(const Stream<dynamic>.empty(), <String>[]),
    );
    final controller = ChatController(socket);

    controller.send('doomed');
    await Future<void>.delayed(const Duration(milliseconds: 80));

    expect(controller.state.first.sendState, SendState.failed);
    expect(controller.state.first.content, 'doomed');
    expect(controller.state.last.role, 'assistant');
    expect(controller.state.last.content, contains('⚠️'));
    expect(controller.isStreaming, isFalse);

    controller.dispose();
    await socket.dispose();
  });
}
