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
  WsSink? _sink;
  Timer? _ping;

  ChatSocket({ChannelFactory? channelFactory})
      : _factory = channelFactory ??
            ((url, headers) =>
                _IoSink(IOWebSocketChannel.connect(url, headers: headers)));

  Stream<ServerFrame> get frames => _frames.stream;

  Future<void> connect(String wsUrl, {required String cookie}) async {
    // IMPORTANT: send the session cookie, and DO NOT send an Origin header
    // (native client → server allows absent Origin; presence triggers CORS).
    final sink = _factory(wsUrl, {'Cookie': cookie});
    _sink = sink;
    sink.stream.listen(
      (data) => _frames.add(parseServerFrame(data.toString())),
      onError: (e) => _frames.add(ErrorFrame(e.toString())),
      onDone: () => _ping?.cancel(),
    );
    _ping = Timer.periodic(
        const Duration(seconds: 30), (_) => _sink?.add(encodePing()));
  }

  void send(String content) => _sink?.add(encodeClientMessage(content));
  void approve(String requestId, bool approved) =>
      _sink?.add(encodeApprovalResponse(requestId, approved));
  void cancel() => _sink?.add(encodeCancel());

  Future<void> dispose() async {
    _ping?.cancel();
    await _sink?.close();
    await _frames.close();
  }
}
