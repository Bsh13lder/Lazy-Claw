import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'chat_message.dart';
import 'chat_socket.dart';
import 'ws_frames.dart';

/// Pure reducer (no IO) so the frame→messages logic is unit-tested.
class ChatReducer {
  final List<ChatMessage> messages = [];
  final StringBuffer _buf = StringBuffer();

  /// Usage metrics from a standalone `usage` frame, attached when `done`
  /// arrives (the `done` payload's own usage wins when both exist).
  UsageInfo? _pendingUsage;

  /// True while an agent turn is streaming — drives the input bar's
  /// stop button.
  bool get isStreaming => messages.any((m) => m.streaming);

  void onUserSend(String text) {
    messages.add(ChatMessage(role: 'user', content: text));
    _buf.clear();
    _pendingUsage = null;
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

  /// Makes sure the live frame has a streaming assistant bubble to land on —
  /// activity can arrive before any token (e.g. a watcher-driven turn).
  void _ensureStreamingBubble() {
    if (messages.isEmpty ||
        (messages.last.role != 'assistant' && messages.last.role != 'plan')) {
      messages.add(
          const ChatMessage(role: 'assistant', content: '', streaming: true));
    }
  }

  void onFrame(ServerFrame f) {
    switch (f) {
      case TokenFrame(:final content):
        if (messages.isEmpty) return;
        _buf.write(content);
        // Visible text flowing ⇒ the reasoning indicator is stale.
        _replaceLast(
            messages.last.copyWith(content: _buf.toString(), thinking: false));

      case DoneFrame(:final content, :final usage):
        if (messages.isEmpty) return;
        final finalText = content.isNotEmpty ? content : _buf.toString();
        _replaceLast(messages.last.copyWith(
          content: finalText,
          streaming: false,
          usage: usage ?? _pendingUsage,
        ));
        _pendingUsage = null;

      case UsageFrame(:final usage):
        // Stash for the terminal done frame (mirrors the web client).
        _pendingUsage = usage;

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
        _replaceLast(messages.last.withApproval(requestId, skill));

      case ToolCallFrame(:final name, :final args, :final toolCallId):
        // Attach the tool activity to the current streaming assistant bubble.
        // If no bubble exists yet, create one.
        _ensureStreamingBubble();
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
        _addBgResult(BackgroundTaskResult(
          name: name,
          taskId: taskId,
          success: true,
          detail: result,
          durationMs: durationMs,
        ));

      case BackgroundFailedFrame(:final name, :final taskId, :final error, :final durationMs):
        _addBgResult(BackgroundTaskResult(
          name: name,
          taskId: taskId,
          success: false,
          detail: error,
          durationMs: durationMs,
        ));

      case PhaseFrame(:final phase):
        // Update the streaming bubble's phase label ("Thinking…", "Acting…")
        // so the user sees the agent's loop progress without a new message.
        _ensureStreamingBubble();
        _replaceLast(messages.last.copyWith(phase: phase));

      case ThinkingDeltaFrame(:final content):
        // Accumulate reasoning into the collapsible "Thinking" section and
        // flip the live "Reasoning…" indicator on.
        _ensureStreamingBubble();
        _replaceLast(messages.last.copyWith(
          thinking: true,
          thinkingText: messages.last.thinkingText + content,
        ));

      case ThinkingDoneFrame():
        if (messages.isEmpty) return;
        if (messages.last.thinking) {
          _replaceLast(messages.last.copyWith(thinking: false));
        }

      case AgentActivityFrame(
          :final kind,
          :final subject,
          :final detail,
          :final done,
          :final failed,
          :final tool
        ):
        _ensureStreamingBubble();
        _replaceLast(messages.last.withAgentActivity(AgentActivity(
          kind: kind,
          subject: subject,
          detail: detail,
          done: done,
          failed: failed,
          events: [detail],
          toolsUsed: tool != null ? [tool] : const [],
        )));

      case PlanPendingFrame(:final plan, :final steps):
        messages.add(ChatMessage(
          role: 'plan',
          content: '',
          planText: plan,
          planSteps: steps,
        ));

      case PlanQuestionFrame(:final question):
        messages.add(ChatMessage(
          role: 'plan',
          content: '',
          planText: question,
          planKind: 'question',
        ));

      case PlanApprovedFrame():
        // Resolve the most recent unresolved plan card so its buttons hide.
        final idx = messages.lastIndexWhere(
            (m) => m.role == 'plan' && m.planKind == 'plan' && !m.planResolved);
        if (idx != -1) {
          messages[idx] = messages[idx].copyWith(planResolved: true);
        }

      case UnknownFrame():
        break; // ignored
    }
  }

  /// Appends a bg_task card, folding in the activity timeline captured for
  /// the same task name and settling that row's spinner.
  void _addBgResult(BackgroundTaskResult card) {
    final located = _findBgActivity(card.name);
    var enriched = card;
    if (located != null) {
      final (msgIdx, activity) = located;
      enriched = BackgroundTaskResult(
        name: card.name,
        taskId: card.taskId,
        success: card.success,
        detail: card.detail,
        durationMs: card.durationMs,
        events: activity.events,
        toolsUsed: activity.toolsUsed,
      );
      if (!activity.done) {
        final updated =
            List<AgentActivity>.from(messages[msgIdx].agentActivities);
        final aIdx = updated.indexOf(activity);
        if (aIdx != -1) {
          updated[aIdx] = activity.settle(success: card.success);
          messages[msgIdx] = messages[msgIdx].withAgentActivities(updated);
        }
      }
    }
    messages.add(ChatMessage(
      role: 'bg_task',
      content: '',
      bgTaskResult: enriched,
    ));
  }

  /// Newest assistant message carrying a 'bg' activity row for [name],
  /// returned with its message index.
  (int, AgentActivity)? _findBgActivity(String name) {
    for (var i = messages.length - 1; i >= 0; i--) {
      final m = messages[i];
      if (m.role != 'assistant') continue;
      for (final a in m.agentActivities) {
        if (a.kind == 'bg' && a.subject == name) return (i, a);
      }
    }
    return null;
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
      default:
        break;
    }
  }

  void send(String text) {
    _reducer.onUserSend(text);
    state = List.unmodifiable(_reducer.messages);
    _socket.send(text);
  }

  /// True while an agent turn is streaming — used by the input bar to swap
  /// in the stop button.
  bool get isStreaming => _reducer.isStreaming;

  /// Cancel the running agent turn. The server replies with a `cancelled`
  /// frame which finalizes the streaming bubble via the reducer.
  void cancel() => _socket.cancel();

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
