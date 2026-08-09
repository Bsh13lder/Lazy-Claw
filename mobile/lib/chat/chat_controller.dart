import 'dart:async';
import 'package:flutter/foundation.dart' show debugPrint;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'chat_message.dart';
import 'chat_socket.dart';
import 'ws_frames.dart';

/// Pure reducer (no IO) so the frame→messages logic is unit-tested.
class ChatReducer {
  final List<ChatMessage> messages = [];
  final StringBuffer _buf = StringBuffer();

  /// Terminal activity outcomes already shown this session, keyed by
  /// `'$kind:$subject'` (AgentActivityFrame carries no task id). A WS
  /// reconnect replays `task_completed` — a terminal frame whose key is here
  /// AND has no live row to settle is a replay and gets dropped instead of
  /// re-creating the card.
  final Set<String> _seenTerminalActivityKeys = {};

  /// Minimum normalized length before a bg result can be considered a
  /// duplicate of a reply — tiny generic strings ("done", "ok") must never
  /// collapse the card.
  static const int _dupMinChars = 24;

  /// Prefix length compared when neither text contains the other whole.
  static const int _dupPrefixChars = 200;

  /// How many recent (non-empty) assistant messages the duplicate check scans.
  static const int _dupScanWindow = 3;

  /// How many recent local messages a history delta-merge scans when matching
  /// a fetched row to an id-less live bubble by content (id adoption).
  static const int _mergeAdoptScanWindow = 20;

  /// Usage metrics from a standalone `usage` frame, attached when `done`
  /// arrives (the `done` payload's own usage wins when both exist).
  UsageInfo? _pendingUsage;

  /// True while a genuine foreground user turn is in flight (set when the
  /// assistant turn starts, cleared on its terminal done/error/cancelled).
  /// Guards [_settleBubbleIfActivitiesDone]: a concurrent background/specialist
  /// terminal must NEVER clear the spinner on a bubble still receiving
  /// foreground `token` frames.
  bool _foregroundActive = false;

  /// True while an agent turn is streaming — drives the input bar's
  /// stop button.
  bool get isStreaming => messages.any((m) => m.streaming);

  /// Adds the user's bubble. When [delivered] is false the socket queued the
  /// message in its offline outbox — the bubble renders a "sending…" hint and
  /// NO assistant spinner starts until [onOutboxFlushed] confirms delivery.
  void onUserSend(String text, {bool delivered = true}) {
    messages.add(ChatMessage(
      role: 'user',
      content: text,
      sendState: delivered ? SendState.sent : SendState.sending,
    ));
    if (delivered) _startAssistantTurn();
  }

  /// Queued messages went out on reconnect: clear their "sending…" marks and
  /// start the assistant streaming bubble (the turn is genuinely live now).
  void onOutboxFlushed(int count) {
    if (count <= 0) return;
    var remaining = count;
    for (var i = 0; i < messages.length && remaining > 0; i++) {
      final m = messages[i];
      if (m.role == 'user' && m.sendState == SendState.sending) {
        messages[i] = m.copyWith(sendState: SendState.sent);
        remaining--;
      }
    }
    // A queued user bubble may sit BELOW the in-flight assistant bubble, so
    // check the whole list — not just messages.last — before spinning up.
    if (!isStreaming) _startAssistantTurn();
  }

  void _startAssistantTurn() {
    _buf.clear();
    _pendingUsage = null;
    _foregroundActive = true;
    messages.add(
        const ChatMessage(role: 'assistant', content: '', streaming: true));
  }

  /// Seeds prior conversation loaded from the backend. Historical messages are
  /// inserted at the FRONT so they always sit above any live messages that may
  /// have already streamed in this session (a brand-new turn is chronologically
  /// newer than the loaded history). No-op for an empty list.
  void seedHistory(List<ChatMessage> history) {
    if (history.isEmpty) return;
    messages.insertAll(0, history);
  }

  /// Delta-merges a freshly fetched history tail (oldest-first) into the
  /// current list. Dedup is by server message id first; a fetched row whose
  /// id is unknown is matched by (role + normalized content) against recent
  /// id-less live bubbles and ADOPTS its id onto the match instead of
  /// duplicating it. Genuinely new rows are inserted at the end — but always
  /// BEFORE a trailing in-flight streaming bubble, because token/done frames
  /// land on `messages.last` and must keep doing so. Rows without an id that
  /// match nothing are dropped (id is the merge contract; inserting them
  /// would duplicate on every re-fetch). Returns true when anything changed.
  bool mergeHistoryTail(List<ChatMessage> tail) {
    if (tail.isEmpty) return false;
    final knownIds = <String>{
      for (final m in messages)
        if (m.id != null && m.id!.isNotEmpty) m.id!,
    };
    var changed = false;
    for (final incoming in tail) {
      final id = incoming.id ?? '';
      if (id.isNotEmpty && knownIds.contains(id)) continue;
      final localIdx = _findMergeMatchByContent(incoming);
      if (localIdx != null) {
        // A still-streaming bubble with the same content is this very row
        // mid-flight — its `done` frame owns finalization; a later merge
        // adopts the id once it settles. Never touch it now.
        if (messages[localIdx].streaming) continue;
        if (id.isEmpty) continue;
        messages[localIdx] =
            messages[localIdx].withServerIdentity(id: id, kind: incoming.kind);
        knownIds.add(id);
        changed = true;
        continue;
      }
      if (id.isEmpty) continue;
      var insertAt = messages.length;
      while (insertAt > 0 && messages[insertAt - 1].streaming) {
        insertAt--;
      }
      messages.insert(insertAt, incoming);
      knownIds.add(id);
      changed = true;
    }
    return changed;
  }

  /// Index of the most recent id-less local message matching [incoming] by
  /// role + whitespace-normalized content, scanning at most
  /// [_mergeAdoptScanWindow] messages back. Null when nothing matches.
  int? _findMergeMatchByContent(ChatMessage incoming) {
    final content = _normalizeWs(incoming.content);
    if (content.isEmpty) return null;
    var scanned = 0;
    for (var i = messages.length - 1;
        i >= 0 && scanned < _mergeAdoptScanWindow;
        i--, scanned++) {
      final m = messages[i];
      if (m.role != incoming.role) continue;
      if (m.id != null && m.id!.isNotEmpty) continue;
      if (_normalizeWs(m.content) == content) return i;
    }
    return null;
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

  /// Non-streaming counterpart of [_ensureStreamingBubble], used when a
  /// terminal activity row must land somewhere: reuse the last assistant
  /// bubble if there is one (its own streaming state is untouched), else
  /// append a quiet, already-finished assistant message. Never flips
  /// `streaming` on, so no phantom spinner can outlive a turn.
  void _ensureSettledBubble() {
    if (messages.isEmpty || messages.last.role != 'assistant') {
      messages.add(const ChatMessage(role: 'assistant', content: ''));
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
        _foregroundActive = false;
        if (messages.isEmpty) return;
        final finalText = content.isNotEmpty ? content : _buf.toString();
        // Terminal frame: a chip whose result never arrived must not spin on.
        _replaceLast(messages.last.withRunningToolsInterrupted().copyWith(
          content: finalText,
          streaming: false,
          usage: usage ?? _pendingUsage,
        ));
        _pendingUsage = null;

      case UsageFrame(:final usage):
        // Stash for the terminal done frame (mirrors the web client).
        _pendingUsage = usage;

      case ErrorFrame(:final message):
        _foregroundActive = false;
        if (messages.isEmpty) {
          messages.add(ChatMessage(
              role: 'assistant', content: '⚠️ $message'));
          return;
        }
        if (messages.last.role == 'user') {
          // No in-flight streaming bubble to finalize, and the user text must
          // never be clobbered. A queued bubble keeps waiting — its failure is
          // owned by the outbox TTL (SendFailedFrame), not a transient drop.
          if (messages.last.sendState == SendState.sending) return;
          messages.add(
              ChatMessage(role: 'assistant', content: '⚠️ $message'));
          return;
        }
        _replaceLast(messages.last
            .withRunningToolsInterrupted()
            .copyWith(content: '⚠️ $message', streaming: false));

      case SendFailedFrame(:final message):
        // A queued outbound message expired/was evicted before delivery —
        // mark the oldest waiting user bubble failed and surface the reason.
        final idx = messages.indexWhere(
            (m) => m.role == 'user' && m.sendState == SendState.sending);
        if (idx != -1) {
          messages[idx] = messages[idx].copyWith(sendState: SendState.failed);
        }
        messages.add(ChatMessage(role: 'assistant', content: '⚠️ $message'));

      case CancelledFrame():
        _foregroundActive = false;
        if (messages.isEmpty) return;
        _replaceLast(messages.last
            .withRunningToolsInterrupted()
            .copyWith(streaming: false));

      case ApprovalRequestFrame(:final requestId, :final skill):
        if (messages.isEmpty) return;
        _replaceLast(messages.last.withApproval(requestId, skill));

      case ToolCallFrame(
          :final name,
          :final args,
          :final toolCallId,
          :final displayName
        ):
        // Attach the tool activity to the current streaming assistant bubble.
        // If no bubble exists yet, create one.
        _ensureStreamingBubble();
        final activity = ToolActivity(
          name: name,
          displayName: displayName,
          args: args,
          toolCallId: toolCallId,
          status: ToolStatus.running,
        );
        _replaceLast(messages.last.withToolCall(activity));

      case ToolResultFrame(:final name, :final preview, :final toolCallId):
        if (messages.isEmpty) return;
        // The chip may not live on messages.last — a plan card or bg_task
        // row can interleave mid-turn. Bounded reverse scan by tool_call_id
        // (name-with-running fallback); unmatched results are dropped.
        final idx = _findToolCallMessage(toolCallId, name);
        if (idx == -1) return;
        messages[idx] = messages[idx].withToolResult(toolCallId, name, preview);
        // A background/watcher bubble whose last chip just settled must not
        // keep spinning (guarded: never fires during a live foreground turn).
        _settleBubbleIfActivitiesDone(idx);

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
          :final tool,
          :final taskId
        ):
        final activity = AgentActivity(
          kind: kind,
          subject: subject,
          taskId: taskId,
          detail: detail,
          done: done,
          failed: failed,
          events: [detail],
          toolsUsed: tool != null ? [tool] : const [],
          currentTool: tool,
        );
        if (done || failed) {
          // Terminal frames settle an existing row in place — if the row
          // lives on an EARLIER message (the turn already finished), it is
          // settled there instead of touching the streaming bubble. Dedup by
          // the stable task_id when present, else the (kind, subject) shape.
          final taskKey = (taskId != null && taskId.isNotEmpty)
              ? '$kind:task:$taskId'
              : null;
          final subjKey = '$kind:$subject';
          final located = _findActivityRow(kind, subject, taskId: taskId);
          if (located != null) {
            if (taskKey != null) _seenTerminalActivityKeys.add(taskKey);
            _seenTerminalActivityKeys.add(subjKey);
            final (msgIdx, existing) = located;
            // Already settled ⇒ a reconnect replay; merging again would only
            // append duplicate event lines.
            if (existing.done) return;
            messages[msgIdx] = messages[msgIdx].withAgentActivity(activity);
            // Bug 2: settling the row may leave the host bubble spinning —
            // clear its spinner when nothing else is still running.
            _settleBubbleIfActivitiesDone(msgIdx);
            return;
          }
          // No row: either a reconnect replay of an outcome we already
          // surfaced (drop it) …
          if ((taskKey != null &&
                  _seenTerminalActivityKeys.contains(taskKey)) ||
              _seenTerminalActivityKeys.contains(subjKey)) {
            return;
          }
          if (taskKey != null) _seenTerminalActivityKeys.add(taskKey);
          _seenTerminalActivityKeys.add(subjKey);
          // … or the FIRST news of a task that finished while the chat was
          // closed (the server never replays background_done). Surface it as
          // a settled row so the completion isn't invisible. Mirrors the
          // non-terminal attach below MINUS the streaming side effect of
          // [_ensureStreamingBubble] — the row arrives done/failed and must
          // render with no spinner and no phantom in-flight bubble.
          _ensureSettledBubble();
          _replaceLast(messages.last.withAgentActivity(activity));
          _settleBubbleIfActivitiesDone(messages.length - 1);
          return;
        }
        _ensureStreamingBubble();
        _replaceLast(messages.last.withAgentActivity(activity));

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

      case NotificationFrame():
        // Deliberately no optimistic paint: the frame may be only a HINT
        // that new server-side rows exist, and its title/body may not match
        // the persisted row text. The controller answers with a history
        // delta-merge (source of truth) — see ChatController._onServerPing.
        break;

      case UnknownFrame():
        break; // ignored
    }
  }

  /// Appends a bg_task card, folding in the activity timeline captured for
  /// the same task name and settling that row's spinner. The outcome key is
  /// recorded so a replayed `task_completed` frame can't re-surface the same
  /// completion as a settled activity row.
  void _addBgResult(BackgroundTaskResult card) {
    _seenTerminalActivityKeys.add('bg:${card.name}');
    // Also register the task-id key so a replayed `task_completed`
    // (task_event_bus) frame for the same task can't re-surface a settled row.
    if (card.taskId != null && card.taskId!.isNotEmpty) {
      _seenTerminalActivityKeys.add('bg:task:${card.taskId}');
    }
    final located = _findActivityRow('bg', card.name, taskId: card.taskId);
    // Heartbeat/scheduled turns deliver the same text twice — consolidated
    // assistant message + background result. Flag the echo so the card
    // collapses to header-only instead of repeating the wall of text.
    final duplicate = _duplicatesRecentReply(card.detail);
    var enriched = BackgroundTaskResult(
      name: card.name,
      taskId: card.taskId,
      success: card.success,
      detail: card.detail,
      durationMs: card.durationMs,
      events: card.events,
      toolsUsed: card.toolsUsed,
      duplicateOfReply: duplicate,
    );
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
        duplicateOfReply: duplicate,
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
      // Bug 2: the host bubble may still be a phantom spinner — settle it once
      // its activity rows are all done and no foreground turn is live.
      _settleBubbleIfActivitiesDone(msgIdx);
    }
    messages.add(ChatMessage(
      role: 'bg_task',
      content: '',
      bgTaskResult: enriched,
    ));
  }

  /// True when [detail] substantially duplicates one of the last
  /// [_dupScanWindow] non-empty assistant replies: after whitespace
  /// normalization, one text contains the other or their first
  /// [_dupPrefixChars] characters match. Deliberately deterministic — no
  /// fuzzy scoring.
  bool _duplicatesRecentReply(String? detail) {
    if (detail == null) return false;
    final d = _normalizeWs(detail);
    if (d.length < _dupMinChars) return false;
    var checked = 0;
    for (var i = messages.length - 1;
        i >= 0 && checked < _dupScanWindow;
        i--) {
      final m = messages[i];
      if (m.role != 'assistant') continue;
      final c = _normalizeWs(m.content);
      if (c.isEmpty) continue;
      checked++;
      if (c.contains(d)) return true;
      if (c.length >= _dupMinChars && d.contains(c)) return true;
      if (d.length >= _dupPrefixChars &&
          c.length >= _dupPrefixChars &&
          d.substring(0, _dupPrefixChars) == c.substring(0, _dupPrefixChars)) {
        return true;
      }
    }
    return false;
  }

  static String _normalizeWs(String s) =>
      s.replaceAll(RegExp(r'\s+'), ' ').trim();

  /// Newest assistant message carrying an activity row for the frame, returned
  /// with its message index. Keyed by the stable [taskId] when present (with a
  /// subject fallback for legacy task_id-less rows that never steals a row
  /// already bound to a different task), else by (kind, subject). Reverse scan
  /// so the most recent turn wins when the same subject appeared earlier too.
  (int, AgentActivity)? _findActivityRow(String kind, String subject,
      {String? taskId}) {
    if (taskId != null && taskId.isNotEmpty) {
      final byId =
          _scanActivityRow((a) => a.kind == kind && a.taskId == taskId);
      if (byId != null) return byId;
      return _scanActivityRow((a) =>
          a.kind == kind && a.taskId == null && a.subject == subject);
    }
    return _scanActivityRow((a) => a.kind == kind && a.subject == subject);
  }

  /// Reverse-scan assistant messages for the first activity row matching [test].
  (int, AgentActivity)? _scanActivityRow(bool Function(AgentActivity) test) {
    for (var i = messages.length - 1; i >= 0; i--) {
      final m = messages[i];
      if (m.role != 'assistant') continue;
      for (final a in m.agentActivities) {
        if (test(a)) return (i, a);
      }
    }
    return null;
  }

  /// How many recent messages the tool-result reverse scan covers — a plan
  /// card or bg_task row can interleave between the chip's bubble and the
  /// list tail, but a genuinely old chip should never be resurrected.
  static const int _toolResultScanWindow = 12;

  /// Index of the newest message carrying the tool chip a result frame
  /// belongs to: exact [toolCallId] match wins; without one (or when the id
  /// is unknown), the newest still-running chip with a matching name.
  /// -1 when nothing matches within the window (the result is dropped).
  int _findToolCallMessage(String? toolCallId, String name) {
    var byName = -1;
    var scanned = 0;
    for (var i = messages.length - 1;
        i >= 0 && scanned < _toolResultScanWindow;
        i--, scanned++) {
      final m = messages[i];
      if (m.toolActivities.isEmpty) continue;
      if (toolCallId != null &&
          m.toolActivities.any((t) => t.toolCallId == toolCallId)) {
        return i;
      }
      if (byName == -1 &&
          m.toolActivities.any(
              (t) => t.name == name && t.status == ToolStatus.running)) {
        byName = i;
      }
    }
    return byName;
  }

  /// Bug 2: after a background/specialist terminal settles a row (or a tool
  /// result settles a chip), clear the host bubble's streaming spinner IFF it
  /// is an assistant bubble that is still `streaming`, carries ≥1 activity
  /// rows or tool chips, ALL of which are settled (agent rows done/failed,
  /// tool chips not running), AND no foreground token turn is live.
  /// Belt-and-suspenders for dropped or reconnect-lost `done` frames — the
  /// server consolidation `done` covers the happy path; this keeps a phantom
  /// bg/watcher bubble from spinning forever.
  void _settleBubbleIfActivitiesDone(int msgIdx) {
    if (_foregroundActive) return;
    if (msgIdx < 0 || msgIdx >= messages.length) return;
    final m = messages[msgIdx];
    if (m.role != 'assistant' || !m.streaming) return;
    if (m.agentActivities.isEmpty && m.toolActivities.isEmpty) return;
    if (!m.agentActivities.every((a) => a.done || a.failed)) return;
    if (m.hasRunningTools) return;
    messages[msgIdx] = m.copyWith(streaming: false);
  }

  void _replaceLast(ChatMessage m) => messages[messages.length - 1] = m;
}

class ChatController extends StateNotifier<List<ChatMessage>> {
  final ChatSocket _socket;
  final ChatReducer _reducer = ChatReducer();
  late final StreamSubscription<ServerFrame> _frameSub;
  late final StreamSubscription<int> _flushSub;
  StreamSubscription<bool>? _connSub;

  // Callback for firing local notifications — injected externally so the
  // controller has no hard dependency on the notification plugin.
  final void Function(String title, String body)? onNotify;

  /// Fetches the freshest chat history tail (oldest-first, already mapped to
  /// [ChatMessage]s) for the initial seed and every delta-merge refresh.
  /// Null (e.g. in reducer-only tests) disables refreshes entirely.
  final Future<List<ChatMessage>> Function()? historyLoader;

  /// Delay before the follow-up refresh after a `notification` frame — the
  /// frame can beat the server's own history write, so one settle-delayed
  /// re-fetch closes that race. Injectable so tests run fast.
  final Duration notificationFollowUp;

  bool _seeded = false;
  bool _refreshing = false;
  bool _refreshQueued = false;
  bool _wasConnected = false;
  Timer? _followUpTimer;

  ChatController(
    this._socket, {
    this.onNotify,
    this.historyLoader,
    this.notificationFollowUp = const Duration(seconds: 2),
  }) : super(const []) {
    _frameSub = _socket.frames.listen((f) {
      _handleNotification(f);
      _reducer.onFrame(f);
      state = List.unmodifiable(_reducer.messages);
      if (f is NotificationFrame) _onServerPing();
    });
    _flushSub = _socket.outboxFlushed.listen((count) {
      _reducer.onOutboxFlushed(count);
      state = List.unmodifiable(_reducer.messages);
    });
    // Every (re)connect may have missed frames while the socket was down —
    // catch up from the history endpoint (the source of truth). Only the
    // down→up edge triggers, so steady-state `true` repeats are free.
    _connSub = _socket.connectionState.listen((up) {
      final was = _wasConnected;
      _wasConnected = up;
      if (up && !was) unawaited(refreshHistory());
    });
  }

  /// Seeds prior conversation on the FIRST call (history inserted above any
  /// live messages); every LATER call delta-merges by message id instead —
  /// so reconnect / resume / notification refreshes can always land without
  /// double-seeding or duplicating. An empty first [history] still counts as
  /// seeded (no prior conversation).
  void seedHistory(List<ChatMessage> history) {
    if (_seeded) {
      if (_reducer.mergeHistoryTail(history)) {
        state = List.unmodifiable(_reducer.messages);
      }
      return;
    }
    _seeded = true;
    if (history.isEmpty) return;
    _reducer.seedHistory(history);
    state = List.unmodifiable(_reducer.messages);
  }

  /// Fetches the freshest history via [historyLoader] and seeds (first time)
  /// or delta-merges it in. Safe to call unconditionally — the stale-chat
  /// fix: the chat screen calls this on mount, on app-lifecycle resume, and
  /// the controller itself on WS reconnect and `notification` frames.
  /// Concurrent triggers coalesce into one trailing re-fetch; errors are
  /// logged and swallowed (best-effort — the next trigger retries).
  Future<void> refreshHistory() async {
    final loader = historyLoader;
    if (loader == null) return;
    if (_refreshing) {
      _refreshQueued = true;
      return;
    }
    _refreshing = true;
    try {
      do {
        _refreshQueued = false;
        try {
          final tail = await loader();
          if (!mounted) return;
          seedHistory(tail);
        } catch (e) {
          // Best-effort: reconnect / resume / next ping retries.
          debugPrint('ChatController.refreshHistory failed: $e');
        }
      } while (_refreshQueued && mounted);
    } finally {
      _refreshing = false;
    }
  }

  /// A `notification` frame arrived: new server-side chat rows (may) exist.
  /// Refresh now, and once more after [notificationFollowUp] in case the
  /// frame outran the server's history write. Follow-ups coalesce.
  void _onServerPing() {
    unawaited(refreshHistory());
    _followUpTimer?.cancel();
    _followUpTimer = Timer(notificationFollowUp, () {
      _followUpTimer = null;
      unawaited(refreshHistory());
    });
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
    // Hand the message to the socket FIRST so the bubble reflects whether it
    // actually went out or sits queued in the offline outbox ("sending…").
    final delivered = _socket.send(text);
    _reducer.onUserSend(text, delivered: delivered);
    state = List.unmodifiable(_reducer.messages);
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
    _followUpTimer?.cancel();
    _frameSub.cancel();
    _flushSub.cancel();
    _connSub?.cancel();
    super.dispose();
  }
}
