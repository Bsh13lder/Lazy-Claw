/// Chat controller for the on-device (local) path.
///
/// Mirrors the server [ChatController] but, instead of a WebSocket, drives the
/// SAME pure [ChatReducer] by SYNTHESIZING the frames the server would emit:
/// a [TokenFrame] per streamed token, a [DoneFrame] at the end, an [ErrorFrame]
/// on failure, a [CancelledFrame] on stop. That means the streaming chat UI —
/// bubbles, spinner, stop button — is reused verbatim; only the token source
/// changes. One turn at a time (the device runs a single inference).
library;

import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../chat/chat_controller.dart' show ChatReducer;
import '../chat/chat_message.dart';
import '../chat/ws_frames.dart';
import 'local_llm_engine.dart';
import 'local_model.dart';

class LocalChatController extends StateNotifier<List<ChatMessage>> {
  LocalChatController(this._engine, {this.systemPrompt}) : super(const []);

  final LocalLlmEngine _engine;

  /// Optional persona/system prompt prepended to every turn.
  final String? systemPrompt;

  final ChatReducer _reducer = ChatReducer();
  StreamSubscription<String>? _gen;
  bool _generating = false;

  bool get isStreaming => _reducer.isStreaming;

  /// Send a user turn and stream the local reply. Ignored while a turn is
  /// already generating (the device handles one inference at a time).
  void send(String text) {
    if (_generating || !_engine.isLoaded) return;
    _reducer.onUserSend(text, delivered: true);
    state = List.unmodifiable(_reducer.messages);
    _run();
  }

  void _run() {
    _generating = true;
    final label =
        localModelById(_engine.loadedModelId ?? '')?.displayName ?? 'Local';
    _gen = _engine.generate(_historyForEngine(), systemPrompt: systemPrompt).listen(
      (tok) {
        _reducer.onFrame(TokenFrame(tok));
        state = List.unmodifiable(_reducer.messages);
      },
      onError: (e) {
        final msg = e is LocalLlmException ? e.message : 'Local generation failed';
        _reducer.onFrame(ErrorFrame(msg));
        state = List.unmodifiable(_reducer.messages);
        _generating = false;
      },
      onDone: () {
        _reducer.onFrame(DoneFrame('', '$label · on-device'));
        state = List.unmodifiable(_reducer.messages);
        _generating = false;
      },
      cancelOnError: true,
    );
  }

  /// The conversation so far as engine turns. Skips the empty streaming
  /// assistant bubble [ChatReducer] just appended for this turn, and any
  /// non-text bubbles (plan/bg_task) the local path never produces.
  List<LocalLlmMessage> _historyForEngine() {
    final out = <LocalLlmMessage>[];
    for (final m in _reducer.messages) {
      if (m.role == 'user') {
        out.add(LocalLlmMessage.user(m.content));
      } else if (m.role == 'assistant' && !m.streaming && m.content.isNotEmpty) {
        out.add(LocalLlmMessage.assistant(m.content));
      }
    }
    return out;
  }

  /// Stop the running local generation and settle the bubble.
  void cancel() {
    _gen?.cancel();
    _gen = null;
    if (_generating) {
      _reducer.onFrame(const CancelledFrame());
      state = List.unmodifiable(_reducer.messages);
      _generating = false;
    }
  }

  @override
  void dispose() {
    _gen?.cancel();
    super.dispose();
  }
}
