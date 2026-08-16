import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/chat_socket.dart';
import 'package:lazyclaw_mobile/chat/ws_frames.dart';

/// A [WsSink] that records when it is closed — proves reconnect tears the
/// old socket down instead of leaking it.
class _ClosingSink implements WsSink {
  @override
  final Stream<dynamic> stream;
  final void Function() onClose;
  _ClosingSink(this.stream, this.onClose);
  @override
  void add(String data) {}
  @override
  Future<void> close() async => onClose();
}

/// A [WsSink] whose [close] NEVER completes — models a half-open channel
/// (dirty network drop) where the close handshake never arrives.
class _HangingCloseSink implements WsSink {
  @override
  final Stream<dynamic> stream;
  _HangingCloseSink(this.stream);
  @override
  void add(String data) {}
  @override
  Future<void> close() => Completer<void>().future; // hangs forever
}

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

  test('reconnect CLOSES the old sink (no zombie sockets)', () async {
    // The storm bug: _open() cancelled the subscription but never closed the
    // old socket, so every reconnect leaked a live connection server-side.
    final closed = <int>[];
    var idx = 0;
    final socket = ChatSocket(
      channelFactory: (url, headers) {
        final id = idx++;
        return _ClosingSink(const Stream.empty(), () => closed.add(id));
      },
    );
    await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc');
    await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc',
        force: true);
    // The first sink (id 0) must have been closed when the second dialled.
    expect(closed, contains(0),
        reason: 'the previous socket must be closed on reconnect');
    await socket.dispose();
  });

  test('verifyAlive reconnects when the socket is already disconnected',
      () async {
    var factoryCalls = 0;
    final controllers = <StreamController<dynamic>>[];
    final socket = ChatSocket(
      channelFactory: (url, headers) {
        factoryCalls++;
        final c = StreamController<dynamic>();
        controllers.add(c);
        return FakeSink(c.stream, <String>[]);
      },
    );
    await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc');
    expect(factoryCalls, 1);
    await controllers.first.close(); // server drops → onDone → _isConnected=false
    await Future<void>.delayed(Duration.zero);
    expect(socket.isConnected, isFalse);

    socket.verifyAlive();
    await Future<void>.delayed(Duration.zero);
    expect(factoryCalls, 2, reason: 'a known-dead socket reconnects at once');
    await socket.dispose();
  });

  test('verifyAlive does NOT reconnect a socket that pongs', () async {
    var factoryCalls = 0;
    final sent = <String>[];
    late StreamController<dynamic> incoming;
    final socket = ChatSocket(
      channelFactory: (url, headers) {
        factoryCalls++;
        incoming = StreamController<dynamic>();
        return FakeSink(incoming.stream, sent);
      },
    );
    await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc');
    expect(factoryCalls, 1);

    socket.verifyAlive();
    // Server pongs the probe ping → a frame arrives → socket proven alive.
    incoming.add('{"type":"pong"}');
    await Future<void>.delayed(const Duration(milliseconds: 10));
    expect(factoryCalls, 1, reason: 'a live socket must not be re-dialled');
    await incoming.close();
    await socket.dispose();
  });

  test('reconnect dials even when the old sink close() never completes',
      () async {
    // Regression 2026-08-16 21:21: _open() awaited old.close(); on a
    // half-open channel (dirty drop, no close handshake) that await hangs
    // forever — ZERO re-dials for 20+ minutes while HTTP worked fine, and
    // the queued message never left. Closing the old sink must never block
    // the new dial.
    var factoryCalls = 0;
    final socket = ChatSocket(
      channelFactory: (url, headers) {
        factoryCalls++;
        return factoryCalls == 1
            ? _HangingCloseSink(const Stream.empty())
            : FakeSink(const Stream.empty(), <String>[]);
      },
    );
    await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc');
    expect(factoryCalls, 1);

    // Force-reconnect must complete promptly and dial a fresh channel even
    // though the first sink's close() hangs forever.
    await socket
        .connect('ws://x/ws/chat', cookie: 'session_id=abc', force: true)
        .timeout(const Duration(seconds: 2));
    expect(factoryCalls, 2,
        reason: 'a hanging close() must not block the reconnect dial');
    await socket.dispose();
  });

  test('force reconnect re-dials a stale-connected socket', () async {
    // Root cause of "messages not delivering after a server restart while
    // backgrounded": the drop fires no onDone (isolate suspended), so
    // _isConnected stays stale-true. A plain connect() to the same
    // url/cookie early-returns (the per-turn guard) and never re-dials, so
    // send() writes to a dead sink silently. On resume we must FORCE a
    // fresh channel.
    var factoryCalls = 0;
    final sinks = <List<String>>[];
    final socket = ChatSocket(
      channelFactory: (url, headers) {
        factoryCalls++;
        final sent = <String>[];
        sinks.add(sent);
        return FakeSink(const Stream.empty(), sent);
      },
    );

    await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc');
    expect(factoryCalls, 1);

    // Same target, not forced → guarded, no re-dial (the bug path).
    await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc');
    expect(factoryCalls, 1, reason: 'guard should skip a redundant reconnect');

    // Forced (resume path) → tears down and re-dials even though the flag
    // still says connected.
    await socket.connect('ws://x/ws/chat', cookie: 'session_id=abc',
        force: true);
    expect(factoryCalls, 2, reason: 'force must re-establish the channel');

    await socket.dispose();
  });
}
