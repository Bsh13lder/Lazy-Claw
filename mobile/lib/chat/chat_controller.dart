import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'chat_message.dart';
import 'chat_socket.dart';
import 'ws_frames.dart';

/// Pure reducer (no IO) so the frame→messages logic is unit-tested.
class ChatReducer {
  final List<ChatMessage> messages = [];
  final StringBuffer _buf = StringBuffer();

  void onUserSend(String text) {
    messages.add(ChatMessage(role: 'user', content: text));
    _buf.clear();
    messages.add(const ChatMessage(role: 'assistant', content: '', streaming: true));
  }

  void onFrame(ServerFrame f) {
    switch (f) {
      case TokenFrame(:final content):
        if (messages.isEmpty) return;
        _buf.write(content);
        _replaceLast(messages.last.copyWith(content: _buf.toString()));
      case DoneFrame(:final content):
        if (messages.isEmpty) return;
        final finalText = content.isNotEmpty ? content : _buf.toString();
        _replaceLast(
            messages.last.copyWith(content: finalText, streaming: false));
      case ErrorFrame(:final message):
        if (messages.isEmpty) {
          messages.add(ChatMessage(
              role: 'assistant', content: '⚠️ $message'));
          return;
        }
        _replaceLast(messages.last
            .copyWith(content: '⚠️ $message', streaming: false));
      case CancelledFrame():
        if (messages.isEmpty) return;
        _replaceLast(messages.last.copyWith(streaming: false));
      case ApprovalRequestFrame(:final requestId, :final skill):
        if (messages.isEmpty) return;
        final last = messages.last;
        messages[messages.length - 1] = ChatMessage(
          role: last.role,
          content: last.content,
          streaming: last.streaming,
          pendingApprovalId: requestId,
          pendingApprovalSkill: skill,
        );
      case UnknownFrame():
        break; // ignored in Milestone A
    }
  }

  void _replaceLast(ChatMessage m) => messages[messages.length - 1] = m;
}

class ChatController extends StateNotifier<List<ChatMessage>> {
  final ChatSocket _socket;
  final ChatReducer _reducer = ChatReducer();
  late final StreamSubscription<ServerFrame> _frameSub;

  ChatController(this._socket) : super(const []) {
    _frameSub = _socket.frames.listen((f) {
      _reducer.onFrame(f);
      state = List.unmodifiable(_reducer.messages);
    });
  }

  void send(String text) {
    _reducer.onUserSend(text);
    state = List.unmodifiable(_reducer.messages);
    _socket.send(text);
  }

  void respondApproval(String id, bool approved) {
    _socket.approve(id, approved);
    // Clear the pending approval on the message so buttons can't be double-tapped.
    final idx = _reducer.messages.indexWhere((m) => m.pendingApprovalId == id);
    if (idx != -1) {
      _reducer.messages[idx] = _reducer.messages[idx].clearApproval();
      state = List.unmodifiable(_reducer.messages);
    }
  }

  @override
  void dispose() {
    _frameSub.cancel();
    super.dispose();
  }
}
