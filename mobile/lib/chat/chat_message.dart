import 'usage_info.dart';

export 'usage_info.dart';

/// Represents a single tool activity entry attached to a message.
class ToolActivity {
  final String name;
  final Map<String, dynamic> args;
  final String? toolCallId;
  final String? resultPreview; // null = still in progress
  const ToolActivity({
    required this.name,
    required this.args,
    this.toolCallId,
    this.resultPreview,
  });

  ToolActivity withResult(String preview) => ToolActivity(
        name: name,
        args: args,
        toolCallId: toolCallId,
        resultPreview: preview,
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
  final String role; // 'user' | 'assistant' | 'bg_task' | 'plan'
  final String content;
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
  ChatMessage _clone({
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

  /// Updates the most recent matching ToolActivity with a result preview.
  ChatMessage withToolResult(String? toolCallId, String name, String preview) {
    final updated = List<ToolActivity>.from(toolActivities);
    // Find by toolCallId first, then fall back to last entry with matching name.
    int idx = toolCallId != null
        ? updated.lastIndexWhere((t) => t.toolCallId == toolCallId)
        : -1;
    if (idx == -1) idx = updated.lastIndexWhere((t) => t.name == name);
    if (idx != -1) {
      updated[idx] = updated[idx].withResult(preview);
    }
    return _clone(toolActivities: updated);
  }
}
