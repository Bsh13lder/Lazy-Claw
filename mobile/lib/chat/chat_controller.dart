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

  /// Seeds prior conversation loaded from the backend. Historical messages are
  /// inserted at the FRONT so they always sit above any live messages that may
  /// have already streamed in this session (a brand-new turn is chronologically
  /// newer than the loaded history). No-op for an empty list.
  void seedHistory(List<ChatMessage> history) {
    if (history.isEmpty) return;
    messages.insertAll(0, history);
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
          toolActivities: last.toolActivities,
        );

      case ToolCallFrame(:final name, :final args, :final toolCallId):
        // Attach the tool activity to the current streaming assistant bubble.
        // If no bubble exists yet, create one.
        if (messages.isEmpty ||
            (messages.last.role != 'assistant' && messages.last.role != 'plan')) {
          messages.add(const ChatMessage(
              role: 'assistant', content: '', streaming: true));
        }
        final activity = ToolActivity(
          name: name,
          args: args,
          toolCallId: toolCallId,
        );
        _replaceLast(messages.last.withToolCall(activity));

      case ToolResultFrame(:final name, :final preview, :final toolCallId):
        if (messages.isEmpty) return;
        _replaceLast(messages.last.withToolResult(toolCallId, name, preview));

      case BackgroundDoneFrame(:final name, :final taskId, :final result, :final durationMs):
        final card = BackgroundTaskResult(
          name: name,
          taskId: taskId,
          success: true,
          detail: result,
          durationMs: durationMs,
        );
        messages.add(ChatMessage(
          role: 'bg_task',
          content: '',
          bgTaskResult: card,
        ));

      case BackgroundFailedFrame(:final name, :final taskId, :final error, :final durationMs):
        final card = BackgroundTaskResult(
          name: name,
          taskId: taskId,
          success: false,
          detail: error,
          durationMs: durationMs,
        );
        messages.add(ChatMessage(
          role: 'bg_task',
          content: '',
          bgTaskResult: card,
        ));

      case PhaseFrame():
        // Phase transitions are informational only — they update the
        // streaming bubble's phase label but don't add a message.
        // Currently ignored at the reducer level; the chat screen could
        // subscribe to the raw frame stream if it wants per-frame animation.
        break;

      case PlanPendingFrame(:final plan, :final steps):
        messages.add(ChatMessage(
          role: 'plan',
          content: '',
          planText: plan,
          planSteps: steps,
        ));

      case ChannelMessageFrame():
        // Channel-message frames are surfaced as local notifications via
        // [_handleNotification]; no chat bubble is added.
        break;

      case UnknownFrame():
        break; // ignored
    }
  }

  void _replaceLast(ChatMessage m) => messages[messages.length - 1] = m;
}

class ChatController extends StateNotifier<List<ChatMessage>> {
  final ChatSocket _socket;
  final ChatReducer _reducer = ChatReducer();
  late final StreamSubscription<ServerFrame> _frameSub;

  // Callback for firing local notifications — injected externally so the
  // controller has no hard dependency on the notification plugin.
  final void Function(String title, String body)? onNotify;

  bool _seeded = false;

  ChatController(this._socket, {this.onNotify}) : super(const []) {
    _frameSub = _socket.frames.listen((f) {
      _handleNotification(f);
      _reducer.onFrame(f);
      state = List.unmodifiable(_reducer.messages);
    });
  }

  /// Seed prior conversation once per controller lifetime (idempotent).
  ///
  /// Called by the chat screen after it fetches history from the backend.
  /// Marks itself done on the first call so a reconnect can't double-seed; an
  /// empty [history] (no prior conversation) still counts as seeded.
  void seedHistory(List<ChatMessage> history) {
    if (_seeded) return;
    _seeded = true;
    if (history.isEmpty) return;
    _reducer.seedHistory(history);
    state = List.unmodifiable(_reducer.messages);
  }

  void _handleNotification(ServerFrame f) {
    switch (f) {
      case BackgroundDoneFrame(:final name, :final result):
        onNotify?.call(
          'Task complete: $name',
          result != null && result.isNotEmpty ? result : 'Done',
        );
      case BackgroundFailedFrame(:final name, :final error):
        onNotify?.call(
          'Task failed: $name',
          error != null && error.isNotEmpty ? error : 'An error occurred',
        );
      case ApprovalRequestFrame(:final skill):
        onNotify?.call(
          'Approval needed',
          'Agent wants to run: $skill',
        );
      case ChannelMessageFrame(:final senderName, :final content):
        onNotify?.call(
          'New message from $senderName',
          content,
        );
      default:
        break;
    }
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
