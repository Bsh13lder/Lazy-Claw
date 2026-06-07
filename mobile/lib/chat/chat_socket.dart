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

class ChatSocket {
  final ChannelFactory _factory;
  final _frames = StreamController<ServerFrame>.broadcast();
  final _connected = StreamController<bool>.broadcast();

  /// Backoff schedule for automatic reconnects. Overridable so tests run fast.
  final Duration baseBackoff;
  final Duration maxBackoff;

  WsSink? _sink;
  StreamSubscription<dynamic>? _sub;
  Timer? _ping;
  Timer? _reconnect;

  // Reconnect bookkeeping — last successful target so we can re-dial it.
  String? _wsUrl;
  String? _cookie;
  int _attempt = 0;
  bool _disposed = false;

  ChatSocket({
    ChannelFactory? channelFactory,
    Duration? baseBackoff,
    Duration? maxBackoff,
  })  : _factory = channelFactory ??
            ((url, headers) =>
                _IoSink(IOWebSocketChannel.connect(url, headers: headers))),
        baseBackoff = baseBackoff ?? const Duration(seconds: 1),
        maxBackoff = maxBackoff ?? const Duration(seconds: 30);

  Stream<ServerFrame> get frames => _frames.stream;

  /// Emits `true` when connected, `false` on disconnect / error.
  /// The socket auto-reconnects after a drop, so a `false` is typically
  /// followed by a `true` once the backoff timer re-establishes the channel.
  Stream<bool> get connectionState => _connected.stream;

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
    _connected.add(true);
  }

  void _onDrop() {
    _ping?.cancel();
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

  void send(String content) => _sink?.add(encodeClientMessage(content));
  void approve(String requestId, bool approved) =>
      _sink?.add(encodeApprovalResponse(requestId, approved));
  void cancel() => _sink?.add(encodeCancel());

  Future<void> dispose() async {
    _disposed = true;
    _ping?.cancel();
    _reconnect?.cancel();
    await _sub?.cancel();
    await _sink?.close();
    await _frames.close();
    await _connected.close();
  }
}
