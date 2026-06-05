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
        _buf.write(content);
        _replaceLast(messages.last.copyWith(content: _buf.toString()));
      case DoneFrame(:final content):
        final finalText = content.isNotEmpty ? content : _buf.toString();
        _replaceLast(
            messages.last.copyWith(content: finalText, streaming: false));
      case ErrorFrame(:final message):
        _replaceLast(messages.last
            .copyWith(content: '⚠️ $message', streaming: false));
      case CancelledFrame():
        _replaceLast(messages.last.copyWith(streaming: false));
      case ApprovalRequestFrame(:final requestId, :final skill):
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
  ChatController(this._socket) : super(const []) {
    _socket.frames.listen((f) {
      _reducer.onFrame(f);
      state = List.unmodifiable(_reducer.messages);
    });
  }

  void send(String text) {
    _reducer.onUserSend(text);
    state = List.unmodifiable(_reducer.messages);
    _socket.send(text);
  }

  void respondApproval(String id, bool approved) =>
      _socket.approve(id, approved);
}
