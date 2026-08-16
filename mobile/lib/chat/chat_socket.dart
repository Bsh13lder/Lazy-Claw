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
  Timer? _resumeProbe;

  // Liveness: set true whenever ANY frame arrives (the server pongs every
  // ping, so a live socket always sees a frame each ping cycle). The ping
  // timer treats a full cycle of silence as a dead socket — this is how a
  // drop that fired no onDone (e.g. dropped while the isolate was suspended)
  // gets detected instead of leaving `_isConnected` stale-true forever.
  bool _sawFrameSincePing = false;

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

  /// True while a live channel is open.
  bool get isConnected => _isConnected;

  /// Cheap app-resume liveness check. Replaces the old
  /// `connect(force: true)` on resume, which re-dialled on EVERY resume
  /// (keyboard, notification — Android fires "resumed" constantly) and, with
  /// the sink leak, buried the server in zombie sockets.
  ///
  /// If we already know we're disconnected, reconnect now. Otherwise send one
  /// ping and wait briefly: the server pongs a live socket, so no reply within
  /// [_resumeProbeTimeout] means the socket died silently (e.g. dropped while
  /// backgrounded) → drop and reconnect. One ping + one short timer per
  /// resume — never a storm.
  static const Duration _resumeProbeTimeout = Duration(seconds: 6);

  void verifyAlive() {
    if (_disposed || _wsUrl == null) return;
    final sink = _sink;
    if (!_isConnected || sink == null) {
      unawaited(_open());
      return;
    }
    _sawFrameSincePing = false;
    try {
      sink.add(encodePing());
    } catch (_) {
      _onDrop();
      return;
    }
    _resumeProbe?.cancel();
    _resumeProbe = Timer(_resumeProbeTimeout, () {
      if (_disposed) return;
      if (!_sawFrameSincePing) _onDrop(); // no pong → dead → reconnect
    });
  }

  Future<void> connect(
    String wsUrl, {
    required String cookie,
    bool force = false,
  }) async {
    // Reconnecting to the SAME endpoint while already connected tore down a
    // working channel and re-dialled it — 150-800ms of TCP+TLS+upgrade on every
    // call, with in-flight frames at risk. The assistant calls this once per
    // turn, so that cost was paid on every question. A genuine change of URL or
    // cookie still reconnects.
    //
    // [force] bypasses the guard for the app-resume path: a drop that happens
    // while the isolate is suspended (server restart while backgrounded) fires
    // no onDone, so `_isConnected` stays stale-true and this guard would skip
    // the reconnect forever — leaving send() writing to a dead sink silently.
    // On resume we can't cheaply prove the socket is alive, so we re-dial.
    if (!force && _isConnected && _wsUrl == wsUrl && _cookie == cookie) return;
    _wsUrl = wsUrl;
    _cookie = cookie;
    _attempt = 0;
    await _open();
  }

  Future<void> _open() async {
    if (_disposed) return;
    // Tear down any previous channel before dialing a fresh one.
    _ping?.cancel();
    _resumeProbe?.cancel();
    await _sub?.cancel();
    // CLOSE the old socket, don't just stop listening. Cancelling _sub ends
    // OUR subscription but leaves the underlying WebSocket open server-side,
    // so repeated reconnects (force-reconnect fired on every app resume) piled
    // up zombie connections — the server logged connect after connect with no
    // disconnect and sends stopped landing (2026-08-16 storm). Closing here
    // makes every reconnect self-cleaning.
    final old = _sink;
    _sink = null;
    if (old != null) {
      // FIRE-AND-FORGET: close() can HANG forever on a half-open channel (a
      // dirty network drop with no close handshake). Awaiting it blocked the
      // reconnect dial entirely — the socket never came back and a queued
      // message sat in "Sending…" for 20+ minutes while HTTP still worked
      // (2026-08-16 21:21). Closing is cleanup, never a prerequisite for
      // dialling the new channel.
      unawaited(old.close().catchError((_) {}));
    }

    // IMPORTANT: send the session cookie, and DO NOT send an Origin header
    // (native client → server allows absent Origin; presence triggers CORS).
    final sink = _factory(_wsUrl!, {
      'Cookie': _cookie!,
    });
    _sink = sink;
    _sub = sink.stream.listen(
      (data) {
        // First byte off a fresh channel means we're healthy — reset backoff.
        _attempt = 0;
        _sawFrameSincePing = true;
        _frames.add(parseServerFrame(data.toString()));
      },
      onError: (e) {
        _frames.add(ErrorFrame(e.toString()));
        _onDrop();
      },
      onDone: _onDrop,
      cancelOnError: true,
    );
    _sawFrameSincePing = true; // grace for the first cycle
    _ping = Timer.periodic(const Duration(seconds: 30), (_) {
      if (!_sawFrameSincePing) {
        // A full cycle with no pong/frame — the socket is dead even though no
        // onDone fired. Drop → reconnect (backoff), instead of pinging a
        // corpse forever.
        _onDrop();
        return;
      }
      _sawFrameSincePing = false;
      _sink?.add(encodePing());
    });
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
    _resumeProbe?.cancel();
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
