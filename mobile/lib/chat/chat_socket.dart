import 'dart:async';
import 'package:web_socket_channel/io.dart';
import 'ws_frames.dart';

/// Minimal seam so the socket is unit-testable without a real server.
abstract class WsSink {
  Stream<dynamic> get stream;
  void add(String data);
  Future<void> close();
}

class FakeSink implements WsSink {
  @override
  final Stream<dynamic> stream;
  final List<String> sent;
  FakeSink(this.stream, this.sent);
  @override
  void add(String data) => sent.add(data);
  @override
  Future<void> close() async {}
}

class _IoSink implements WsSink {
  final IOWebSocketChannel _ch;
  _IoSink(this._ch);
  @override
  Stream<dynamic> get stream => _ch.stream;
  @override
  void add(String data) => _ch.sink.add(data);
  @override
  Future<void> close() async => _ch.sink.close();
}

typedef ChannelFactory = WsSink Function(String url, Map<String, String> headers);

/// One outbound frame waiting in the pending outbox for a reconnect.
/// Carries its own expiry timer so an undelivered message fails visibly
/// (via [SendFailedFrame]) instead of waiting forever.
class _PendingSend {
  final String encoded;
  Timer? expiry;
  _PendingSend(this.encoded);
}

class ChatSocket {
  /// Most messages a disconnected session may queue; the oldest is evicted
  /// (with a visible [SendFailedFrame]) when a new send would exceed it.
  static const int maxOutbox = 20;

  final ChannelFactory _factory;
  final _frames = StreamController<ServerFrame>.broadcast();
  final _connected = StreamController<bool>.broadcast();
  final _outboxFlushed = StreamController<int>.broadcast();

  /// Backoff schedule for automatic reconnects. Overridable so tests run fast.
  final Duration baseBackoff;
  final Duration maxBackoff;

  /// How long a queued (offline) send may wait for a reconnect before it is
  /// dropped with a visible [SendFailedFrame]. Overridable for tests.
  final Duration outboxTtl;

  WsSink? _sink;
  StreamSubscription<dynamic>? _sub;
  Timer? _ping;
  Timer? _reconnect;

  // Reconnect bookkeeping — last successful target so we can re-dial it.
  String? _wsUrl;
  String? _cookie;
  int _attempt = 0;
  bool _disposed = false;

  // True between a successful _open and the next drop. send() must check
  // this (not just _sink != null): after a drop the stale sink still exists
  // but writes to it vanish silently.
  bool _isConnected = false;

  // FIFO outbox of encoded messages sent while disconnected.
  final List<_PendingSend> _outbox = [];

  ChatSocket({
    ChannelFactory? channelFactory,
    Duration? baseBackoff,
    Duration? maxBackoff,
    Duration? outboxTtl,
  })  : _factory = channelFactory ??
            ((url, headers) =>
                _IoSink(IOWebSocketChannel.connect(url, headers: headers))),
        baseBackoff = baseBackoff ?? const Duration(seconds: 1),
        maxBackoff = maxBackoff ?? const Duration(seconds: 30),
        outboxTtl = outboxTtl ?? const Duration(seconds: 60);

  Stream<ServerFrame> get frames => _frames.stream;

  /// Emits `true` when connected, `false` on disconnect / error.
  /// The socket auto-reconnects after a drop, so a `false` is typically
  /// followed by a `true` once the backoff timer re-establishes the channel.
  Stream<bool> get connectionState => _connected.stream;

  /// Emits the number of queued messages delivered when a reconnect flushes
  /// the pending outbox — lets the controller clear "sending…" bubbles and
  /// start the assistant turn only once the message actually went out.
  Stream<int> get outboxFlushed => _outboxFlushed.stream;

  Future<void> connect(String wsUrl, {required String cookie}) async {
    _wsUrl = wsUrl;
    _cookie = cookie;
    _attempt = 0;
    await _open();
  }

  Future<void> _open() async {
    if (_disposed) return;
    // Tear down any previous channel before dialing a fresh one.
    _ping?.cancel();
    await _sub?.cancel();

    // IMPORTANT: send the session cookie, and DO NOT send an Origin header
    // (native client → server allows absent Origin; presence triggers CORS).
    final sink = _factory(_wsUrl!, {'Cookie': _cookie!});
    _sink = sink;
    _sub = sink.stream.listen(
      (data) {
        // First byte off a fresh channel means we're healthy — reset backoff.
        _attempt = 0;
        _frames.add(parseServerFrame(data.toString()));
      },
      onError: (e) {
        _frames.add(ErrorFrame(e.toString()));
        _onDrop();
      },
      onDone: _onDrop,
      cancelOnError: true,
    );
    _ping = Timer.periodic(
        const Duration(seconds: 30), (_) => _sink?.add(encodePing()));
    _isConnected = true;
    _connected.add(true);
    _flushOutbox(sink);
  }

  /// Delivers any messages queued while disconnected, oldest first.
  void _flushOutbox(WsSink sink) {
    if (_outbox.isEmpty) return;
    final pending = List<_PendingSend>.of(_outbox);
    _outbox.clear();
    for (final p in pending) {
      p.expiry?.cancel();
      sink.add(p.encoded);
    }
    _outboxFlushed.add(pending.length);
  }

  void _onDrop() {
    _ping?.cancel();
    _isConnected = false;
    if (_disposed) return;
    _connected.add(false);
    // Finalize any in-flight streaming bubble so the UI doesn't spin forever.
    _frames.add(const ErrorFrame('Disconnected'));
    _scheduleReconnect();
  }

  void _scheduleReconnect() {
    if (_disposed || _wsUrl == null) return;
    _reconnect?.cancel();
    // Exponential backoff: base, 2×, 4× … capped at maxBackoff. Shift is
    // bounded so the bit-shift can never overflow on a long-lived outage.
    final shift = _attempt.clamp(0, 16);
    final ms = (baseBackoff.inMilliseconds * (1 << shift))
        .clamp(baseBackoff.inMilliseconds, maxBackoff.inMilliseconds);
    _attempt += 1;
    _reconnect = Timer(Duration(milliseconds: ms), () {
      if (!_disposed) _open();
    });
  }

  /// Sends [content], or queues it if the socket is disconnected.
  ///
  /// Returns `true` when the frame was written immediately, `false` when it
  /// was queued in the pending outbox (the caller should show a "sending…"
  /// state). Queued messages flush FIFO on the next reconnect; one that is
  /// still undelivered after [outboxTtl] is dropped with a [SendFailedFrame]
  /// so the failure is visible and the user can resend.
  bool send(String content) {
    final encoded = encodeClientMessage(content);
    if (_isConnected && _sink != null) {
      _sink!.add(encoded);
      return true;
    }
    _enqueue(encoded);
    return false;
  }

  void _enqueue(String encoded) {
    if (_outbox.length >= maxOutbox) {
      final evicted = _outbox.removeAt(0);
      evicted.expiry?.cancel();
      _frames.add(const SendFailedFrame(
          'Message dropped — too many messages queued while offline. '
          'Please resend it.'));
    }
    final pending = _PendingSend(encoded);
    pending.expiry = Timer(outboxTtl, () => _expirePending(pending));
    _outbox.add(pending);
  }

  /// TTL hit before a reconnect flushed [pending] — drop it visibly.
  void _expirePending(_PendingSend pending) {
    if (_disposed) return;
    if (!_outbox.remove(pending)) return; // already flushed or evicted
    _frames.add(const SendFailedFrame(
        'Message could not be delivered — connection lost. Please resend it.'));
  }

  void approve(String requestId, bool approved) =>
      _sink?.add(encodeApprovalResponse(requestId, approved));
  void cancel() => _sink?.add(encodeCancel());

  Future<void> dispose() async {
    _disposed = true;
    _ping?.cancel();
    _reconnect?.cancel();
    for (final p in _outbox) {
      p.expiry?.cancel();
    }
    _outbox.clear();
    await _sub?.cancel();
    await _sink?.close();
    await _frames.close();
    await _connected.close();
    await _outboxFlushed.close();
  }
}
