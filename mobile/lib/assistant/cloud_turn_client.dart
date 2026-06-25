/// Runs one "Hey Lazy" turn against the real LazyClaw server agent over a
/// DEDICATED ChatSocket (isolated from the main-chat socket so assistant turns
/// never appear in the Chat tab). Yields reply tokens; throws on server error.
library;

import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../chat/chat_socket.dart';
import '../chat/ws_frames.dart';
import '../core/config/server_config.dart';
import '../providers/auth_provider.dart'; // apiClientProvider, baseUrlProvider

class CloudTurnException implements Exception {
  final String message;
  const CloudTurnException(this.message);
  @override
  String toString() => 'CloudTurnException: $message';
}

/// Narrow seam the assistant controller depends on, so tests can inject a fake
/// without constructing a real socket. [CloudTurnClient] is the only production
/// implementation.
abstract interface class CloudTurns {
  Stream<String> streamTurn(String text);
}

class CloudTurnClient implements CloudTurns {
  CloudTurnClient(this._socket);
  final ChatSocket _socket;

  /// Sends [userText] and yields reply tokens until the server's DoneFrame.
  /// Throws [CloudTurnException] on ErrorFrame / SendFailedFrame.
  @override
  Stream<String> streamTurn(String userText) {
    // Subscribe BEFORE sending so no early token is missed.
    final sub = _socket.frames;
    final controller = StreamController<String>();
    late final StreamSubscription<ServerFrame> listen;
    // Did any live token reach the consumer? On quiet-mode / non-streaming
    // turns (the tool / channel / action turns the router hard-escalates to
    // cloud) the server forwards NO `token` frames and delivers the whole
    // reply in the terminal `done` payload (chat_ws.py:
    // `{"type":"done","content": result or cb._buffer}`). We must surface that
    // content, mirroring the main-chat reducer's
    // `content.isNotEmpty ? content : buffer`. Without this the reply is
    // silently dropped — the server "does the work" but nothing shows.
    var streamedAny = false;
    listen = sub.listen((f) {
      switch (f) {
        case TokenFrame(:final content):
          if (content.isNotEmpty) {
            streamedAny = true;
            controller.add(content);
          }
        case DoneFrame(:final content):
          // Emit the terminal content only when nothing streamed live, so a
          // turn that DID stream tokens doesn't get its full text appended a
          // second time (the done payload repeats the streamed text).
          if (!streamedAny && content.isNotEmpty) {
            controller.add(content);
          }
          controller.close();
        case ErrorFrame(:final message):
          controller.addError(CloudTurnException(message));
          controller.close();
        case SendFailedFrame(:final message):
          controller.addError(CloudTurnException(message));
          controller.close();
        default:
          break; // ignore tool/phase/activity frames for the voice MVP
      }
    });
    controller.onCancel = () => listen.cancel();
    _socket.send(userText);
    return controller.stream;
  }
}

/// DEDICATED socket for assistant cloud turns — separate from chatSocketProvider.
final assistantSocketProvider = Provider<ChatSocket>((ref) {
  final s = ChatSocket();
  ref.onDispose(s.dispose);
  return s;
});

final cloudTurnClientProvider = Provider<CloudTurnClient>(
  (ref) => CloudTurnClient(ref.watch(assistantSocketProvider)),
);

/// Connects the dedicated assistant socket using the same base URL + session
/// cookie the chat screen uses. Returns false when there is no session.
Future<bool> ensureAssistantSocketConnected(Ref ref) async {
  final base = ref.read(baseUrlProvider);
  final cookie = await ref.read(apiClientProvider).getSessionCookie();
  if (cookie == null) return false;
  await ref.read(assistantSocketProvider).connect(
        ServerConfig.wsUrlFor(base),
        cookie: 'session_id=$cookie',
      );
  return true;
}
