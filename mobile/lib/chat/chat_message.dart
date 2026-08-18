import 'usage_info.dart';

export 'usage_info.dart';

/// Lifecycle state of a tool-activity chip.
///
/// [running] is ONLY ever produced by a live `tool_call` frame — history
/// mapping must never mint it (a history chip has no result stream to settle
/// it, so a running history chip would spin forever). [interrupted] marks a
/// chip whose result never arrived before the turn's terminal frame (done /
/// error / cancelled). [unknown] marks history rows where the server could
/// not tell whether the call finished.
enum ToolStatus { running, done, error, interrupted, unknown }

/// Represents a single tool activity entry attached to a message.
class ToolActivity {
  final String name;

  /// Human-friendly label from the server (`display` on history rows,
  /// `display_name` on WS frames). Null on old servers — the UI falls back
  /// to [name].
  final String? displayName;
  final Map<String, dynamic> args;
  final String? toolCallId;
  final String? resultPreview;
  final ToolStatus status;

  const ToolActivity({
    required this.name,
    required this.args,
    this.displayName,
    this.toolCallId,
    this.resultPreview,
    this.status = ToolStatus.running,
  });

  /// Returns a settled copy carrying the tool result. Immutable — the
  /// original instance is untouched.
  ToolActivity withResult(String preview) => ToolActivity(
        name: name,
        displayName: displayName,
        args: args,
        toolCallId: toolCallId,
        resultPreview: preview,
        status: ToolStatus.done,
      );

  /// Returns a copy with only [status] replaced.
  ToolActivity withStatus(ToolStatus next) => ToolActivity(
        name: name,
        displayName: displayName,
        args: args,
        toolCallId: toolCallId,
        resultPreview: resultPreview,
        status: next,
      );
}

/// One live "what the agent is doing" row shown under a streaming bubble:
/// delegation, specialist progress, background-task or browser activity.
/// Upserted by [subject] so each specialist/background task holds a single
/// row, while [events] accumulates the chronological timeline behind it.
class AgentActivity {
  final String kind; // 'delegate' | 'specialist' | 'bg' | 'browser'
  final String subject;

  /// Stable server-minted task id (task_* / bg_* frames). When present it,
  /// not [subject], is the upsert key — task_step/phase/completed carry no
  /// name so their subject degrades to 'task', but their task_id still binds
  /// them to the row task_started opened. Null for subject-keyed rows
  /// (specialist / delegate / browser).
  final String? taskId;

  /// Latest state line, e.g. 'using web_search'.
  final String detail;
  final bool done;
  final bool failed;

  /// Chronological detail lines (oldest → newest) for the expandable timeline.
  final List<String> events;

  /// Tool names in call order — drives the "N tools" summary.
  final List<String> toolsUsed;

  /// Tool executing RIGHT NOW (latest tool frame); null when the newest
  /// frame carried no tool — i.e. the row is idle or between tools.
  final String? currentTool;

  const AgentActivity({
    required this.kind,
    required this.subject,
    required this.detail,
    this.taskId,
    this.done = false,
    this.failed = false,
    this.events = const [],
    this.toolsUsed = const [],
    this.currentTool,
  });

  /// Fold a newer event for the same subject into this row: latest detail /
  /// terminal flags / currentTool win, histories concatenate (tools deduped).
  /// Returns a new instance.
  AgentActivity merge(AgentActivity next) => AgentActivity(
        kind: kind,
        subject: subject,
        taskId: next.taskId ?? taskId,
        detail: next.detail,
        done: next.done,
        failed: next.failed,
        events: [...events, ...next.events],
        toolsUsed: [
          ...toolsUsed,
          ...next.toolsUsed.where((t) => !toolsUsed.contains(t)),
        ],
        currentTool: next.currentTool,
      );

  /// Copy with terminal flags forced (used when a background_done /
  /// background_failed frame settles the matching activity row).
  AgentActivity settle({required bool success}) => AgentActivity(
        kind: kind,
        subject: subject,
        taskId: taskId,
        detail: success ? 'finished' : 'failed',
        done: true,
        failed: !success,
        events: [...events, success ? 'finished' : 'failed'],
        toolsUsed: toolsUsed,
        currentTool: null,
      );
}

/// Represents the outcome of a background task.
class BackgroundTaskResult {
  final String name;
  final String? taskId;
  final bool success;
  final String? detail; // result text or error text
  final int? durationMs;

  /// Chronological activity lines captured while the task ran (from the
  /// matching [AgentActivity] row) — renders as an expandable log.
  final List<String> events;
  final List<String> toolsUsed;

  /// True when the reducer detected that [detail] substantially repeats a
  /// recent assistant reply (heartbeat/scheduled tasks deliver the same text
  /// as a consolidated message AND a background result). The card still
  /// renders — the user sees the task settled — but header-only, without
  /// repeating the wall of text.
  final bool duplicateOfReply;

  const BackgroundTaskResult({
    required this.name,
    required this.success,
    this.taskId,
    this.detail,
    this.durationMs,
    this.events = const [],
    this.toolsUsed = const [],
    this.duplicateOfReply = false,
  });
}

/// Delivery state of an outbound (user) message.
///
/// `sent` is the default — the socket wrote the frame immediately.
/// `sending` means the socket was disconnected and the message sits in the
/// pending outbox awaiting a reconnect (bubble shows a "Sending…" hint).
/// `failed` means the outbox TTL expired before delivery (bubble shows a
/// "Not delivered" hint; the user retries by resending).
enum SendState { sent, sending, failed }

class ChatMessage {
  /// Server-side message id from the history endpoint. Null for live bubbles
  /// minted from WS frames — a later history delta-merge adopts the id onto
  /// them so re-fetches dedupe instead of duplicating.
  final String? id;

  /// Optional server row kind. `'notification'` marks a proactive server ping
  /// (reminder / cron result / watcher alert) persisted as an assistant row —
  /// renders with the bell treatment. Null / anything else = normal bubble.
  final String? kind;

  /// Server ids of the rows this bubble ABSORBED when a batch-persisted agent
  /// turn was collapsed into one bubble (see `chat/turn_merge.dart`). These
  /// ids never render on their own again, so the history delta-merge MUST
  /// treat them as already-known — otherwise every re-fetch would re-insert
  /// the absorbed interim rows as duplicates.
  final List<String> absorbedIds;

  final String role; // 'user' | 'assistant' | 'bg_task' | 'plan'
  final String content;

  /// When the message was created — parsed from the server row's
  /// `created_at` for history messages, stamped locally for live bubbles.
  /// Null only for legacy rows with unparseable timestamps.
  final DateTime? createdAt;
  final bool streaming;
  // Outbound delivery state (meaningful for role == 'user' only).
  final SendState sendState;
  final String? pendingApprovalId;
  final String? pendingApprovalSkill;
  // Tool activity chips shown under a streaming/done assistant bubble.
  final List<ToolActivity> toolActivities;
  // Live agent activity rows (delegation / specialists / background work).
  final List<AgentActivity> agentActivities;
  // Current TAOR phase ('think'|'act'|'observe'|'reflect') while streaming.
  final String? phase;
  // Extended-thinking in progress (shows a "Reasoning…" indicator).
  final bool thinking;
  // Accumulated extended-thinking text (collapsible "Thinking" section).
  final String thinkingText;
  // Token/cost metrics attached when the turn completes.
  final UsageInfo? usage;
  // Background task result card (role == 'bg_task').
  final BackgroundTaskResult? bgTaskResult;
  // Plan pending (role == 'plan').
  final String? planText;
  final List<String> planSteps;
  // 'plan' (approve/reject gate) or 'question' (answer via normal message).
  final String planKind;
  // True once the server confirmed approval (hides the action buttons).
  final bool planResolved;

  const ChatMessage({
    required this.role,
    required this.content,
    this.id,
    this.kind,
    this.absorbedIds = const [],
    this.createdAt,
    this.streaming = false,
    this.sendState = SendState.sent,
    this.pendingApprovalId,
    this.pendingApprovalSkill,
    this.toolActivities = const [],
    this.agentActivities = const [],
    this.phase,
    this.thinking = false,
    this.thinkingText = '',
    this.usage,
    this.bgTaskResult,
    this.planText,
    this.planSteps = const [],
    this.planKind = 'plan',
    this.planResolved = false,
  });

  /// Single cloning seam — every public copy helper delegates here so a new
  /// field only has to be threaded through once.
  /// User-facing text: internal reasoning blocks stripped. The server
  /// strips these from history at read time, but LIVE streamed tokens
  /// arrive raw — without this, a wall of `<plan>` XML leads every
  /// streaming reply (2026-08-14 "chat is a mess").
  String get displayContent {
    if (role != 'assistant' || !content.contains('<')) return content;
    var out = content.replaceAll(_internalBlockRe, '');
    out = out.replaceAll(_internalBareTagRe, '');
    return out.trimLeft();
  }

  static final RegExp _internalBlockRe = RegExp(
    r'<(plan|taor_plan|think)>.*?</\1>\s*',
    dotAll: true,
  );
  static final RegExp _internalBareTagRe =
      RegExp(r'</?(plan|taor_plan|think)>\s*');

  ChatMessage _clone({
    String? id,
    String? kind,
    List<String>? absorbedIds,
    DateTime? createdAt,
    String? content,
    bool? streaming,
    SendState? sendState,
    String? phase,
    bool? thinking,
    String? thinkingText,
    UsageInfo? usage,
    bool? planResolved,
    bool clearApprovalFields = false,
    String? pendingApprovalId,
    String? pendingApprovalSkill,
    List<ToolActivity>? toolActivities,
    List<AgentActivity>? agentActivities,
  }) =>
      ChatMessage(
        role: role,
        content: content ?? this.content,
        id: id ?? this.id,
        kind: kind ?? this.kind,
        absorbedIds: absorbedIds ?? this.absorbedIds,
        createdAt: createdAt ?? this.createdAt,
        streaming: streaming ?? this.streaming,
        sendState: sendState ?? this.sendState,
        pendingApprovalId: clearApprovalFields
            ? null
            : (pendingApprovalId ?? this.pendingApprovalId),
        pendingApprovalSkill: clearApprovalFields
            ? null
            : (pendingApprovalSkill ?? this.pendingApprovalSkill),
        toolActivities: toolActivities ?? this.toolActivities,
        agentActivities: agentActivities ?? this.agentActivities,
        phase: phase ?? this.phase,
        thinking: thinking ?? this.thinking,
        thinkingText: thinkingText ?? this.thinkingText,
        usage: usage ?? this.usage,
        bgTaskResult: bgTaskResult,
        planText: planText,
        planSteps: planSteps,
        planKind: planKind,
        planResolved: planResolved ?? this.planResolved,
      );

  ChatMessage copyWith({
    String? content,
    bool? streaming,
    SendState? sendState,
    String? phase,
    bool? thinking,
    String? thinkingText,
    UsageInfo? usage,
    bool? planResolved,
  }) =>
      _clone(
        content: content,
        streaming: streaming,
        sendState: sendState,
        phase: phase,
        thinking: thinking,
        thinkingText: thinkingText,
        usage: usage,
        planResolved: planResolved,
      );

  /// Attach a pending approval prompt to this message.
  ChatMessage withApproval(String requestId, String skill) => _clone(
        pendingApprovalId: requestId,
        pendingApprovalSkill: skill,
      );

  /// Adopts the server-side identity of a history row onto a live bubble
  /// minted from WS frames (delta-merge dedup: once the id is attached,
  /// later re-fetches recognize this message instead of duplicating it).
  /// [absorbedIds] UNIONS with whatever this message already absorbed — an
  /// empty/absent list never clears the existing set.
  ChatMessage withServerIdentity({
    String? id,
    String? kind,
    DateTime? createdAt,
    List<String>? absorbedIds,
  }) =>
      _clone(
        id: id,
        kind: kind,
        createdAt: createdAt,
        absorbedIds: (absorbedIds == null || absorbedIds.isEmpty)
            ? null
            : <String>{...this.absorbedIds, ...absorbedIds}
                .toList(growable: false),
      );

  /// Returns the collapsed form of a batch-persisted agent turn: this row (the
  /// LAST of the turn) keeps its identity while carrying the turn's final
  /// [content], the union of its tool chips and the ids of the rows it
  /// absorbed. Built by `mergeTurnRows` — see `chat/turn_merge.dart`.
  ChatMessage withAbsorbedRows({
    required String content,
    required List<ToolActivity> toolActivities,
    required List<String> absorbedIds,
  }) =>
      _clone(
        content: content,
        toolActivities: toolActivities,
        absorbedIds: absorbedIds,
      );

  /// Returns a copy with approval fields cleared (prevents double-tap).
  ChatMessage clearApproval() => _clone(clearApprovalFields: true);

  /// Upserts an [AgentActivity]: keyed by (kind, taskId) when the incoming
  /// activity carries a task_id — so task_step/phase/completed (whose subject
  /// degrades to 'task') fold onto the row task_started opened — else by
  /// (kind, subject) for the subject-keyed specialist/browser rows. A row
  /// already bound to a DIFFERENT task_id is never stolen by the subject
  /// fallback. Missing rows are appended.
  ChatMessage withAgentActivity(AgentActivity activity) {
    final updated = List<AgentActivity>.from(agentActivities);
    int idx = -1;
    if (activity.taskId != null) {
      idx = updated.indexWhere(
          (a) => a.kind == activity.kind && a.taskId == activity.taskId);
      if (idx == -1) {
        // Legacy row minted before task_id threading — match by subject, but
        // only if it isn't already owned by another task.
        idx = updated.indexWhere((a) =>
            a.kind == activity.kind &&
            a.taskId == null &&
            a.subject == activity.subject);
      }
    } else {
      idx = updated.indexWhere(
          (a) => a.kind == activity.kind && a.subject == activity.subject);
    }
    if (idx == -1) {
      updated.add(activity);
    } else {
      updated[idx] = updated[idx].merge(activity);
    }
    return _clone(agentActivities: updated);
  }

  /// Replaces the agent-activity list wholesale (used to settle a row when
  /// its background task completes).
  ChatMessage withAgentActivities(List<AgentActivity> activities) =>
      _clone(agentActivities: activities);

  /// Appends a new pending ToolActivity (no result yet).
  ChatMessage withToolCall(ToolActivity activity) =>
      _clone(toolActivities: [...toolActivities, activity]);

  /// Updates the most recent matching ToolActivity with a result preview
  /// (which also settles its status to done).
  ChatMessage withToolResult(String? toolCallId, String name, String preview) {
    final updated = List<ToolActivity>.from(toolActivities);
    // Find by toolCallId first, then prefer the newest still-running entry
    // with a matching name, then any entry with a matching name.
    int idx = toolCallId != null
        ? updated.lastIndexWhere((t) => t.toolCallId == toolCallId)
        : -1;
    if (idx == -1) {
      idx = updated.lastIndexWhere(
          (t) => t.name == name && t.status == ToolStatus.running);
    }
    if (idx == -1) idx = updated.lastIndexWhere((t) => t.name == name);
    if (idx != -1) {
      updated[idx] = updated[idx].withResult(preview);
    }
    return _clone(toolActivities: updated);
  }

  /// True when any tool chip is still marked running.
  bool get hasRunningTools =>
      toolActivities.any((t) => t.status == ToolStatus.running);

  /// Returns a copy with every still-running tool chip marked interrupted —
  /// terminal frames (done / error / cancelled) apply this so a chip whose
  /// result never arrived can't spin forever. Settled chips are untouched;
  /// returns `this` unchanged when nothing is running.
  ChatMessage withRunningToolsInterrupted() {
    if (!hasRunningTools) return this;
    return _clone(
      toolActivities: [
        for (final t in toolActivities)
          t.status == ToolStatus.running
              ? t.withStatus(ToolStatus.interrupted)
              : t,
      ],
    );
  }

  /// Settle every still-spinning NON-background agent-activity row.
  ///
  /// Called on turn-end frames (done / error). A sync delegation or
  /// specialist cannot outlive its turn, but a `delegate`-kind row has no
  /// terminal frame in the wire contract at all (team_delegate mints it;
  /// the specialist completes under its own subject) — 2026-08-18: the
  /// "whatsapp · delegated" chip spun forever after the reply was sent.
  /// `bg` rows are exempt: background work legitimately outlives the turn
  /// and settles via its own background_done / task_completed frames.
  ChatMessage withSyncActivitiesSettled() {
    final hasSpinning =
        agentActivities.any((a) => a.kind != 'bg' && !a.done && !a.failed);
    if (!hasSpinning) return this;
    return _clone(
      agentActivities: [
        for (final a in agentActivities)
          (a.kind != 'bg' && !a.done && !a.failed)
              ? a.merge(AgentActivity(
                  kind: a.kind,
                  subject: a.subject,
                  taskId: a.taskId,
                  detail: 'finished',
                  done: true,
                ))
              : a,
      ],
    );
  }
}
