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

/// Represents the outcome of a background task.
class BackgroundTaskResult {
  final String name;
  final String? taskId;
  final bool success;
  final String? detail; // result text or error text
  final int? durationMs;
  const BackgroundTaskResult({
    required this.name,
    required this.success,
    this.taskId,
    this.detail,
    this.durationMs,
  });
}

class ChatMessage {
  final String role; // 'user' | 'assistant' | 'bg_task' | 'plan'
  final String content;
  final bool streaming;
  final String? pendingApprovalId;
  final String? pendingApprovalSkill;
  // Tool activity chips shown under a streaming/done assistant bubble.
  final List<ToolActivity> toolActivities;
  // Background task result card (role == 'bg_task').
  final BackgroundTaskResult? bgTaskResult;
  // Plan pending (role == 'plan').
  final String? planText;
  final List<String> planSteps;

  const ChatMessage({
    required this.role,
    required this.content,
    this.streaming = false,
    this.pendingApprovalId,
    this.pendingApprovalSkill,
    this.toolActivities = const [],
    this.bgTaskResult,
    this.planText,
    this.planSteps = const [],
  });

  ChatMessage copyWith({String? content, bool? streaming}) => ChatMessage(
        role: role,
        content: content ?? this.content,
        streaming: streaming ?? this.streaming,
        pendingApprovalId: pendingApprovalId,
        pendingApprovalSkill: pendingApprovalSkill,
        toolActivities: toolActivities,
        bgTaskResult: bgTaskResult,
        planText: planText,
        planSteps: planSteps,
      );

  /// Returns a copy with approval fields cleared (prevents double-tap).
  ChatMessage clearApproval() => ChatMessage(
        role: role,
        content: content,
        streaming: streaming,
        // pendingApprovalId and pendingApprovalSkill intentionally omitted → null
        toolActivities: toolActivities,
        bgTaskResult: bgTaskResult,
        planText: planText,
        planSteps: planSteps,
      );

  /// Appends a new pending ToolActivity (no result yet).
  ChatMessage withToolCall(ToolActivity activity) => ChatMessage(
        role: role,
        content: content,
        streaming: streaming,
        pendingApprovalId: pendingApprovalId,
        pendingApprovalSkill: pendingApprovalSkill,
        toolActivities: [...toolActivities, activity],
        bgTaskResult: bgTaskResult,
        planText: planText,
        planSteps: planSteps,
      );

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
    return ChatMessage(
      role: role,
      content: content,
      streaming: streaming,
      pendingApprovalId: pendingApprovalId,
      pendingApprovalSkill: pendingApprovalSkill,
      toolActivities: updated,
      bgTaskResult: bgTaskResult,
      planText: planText,
      planSteps: planSteps,
    );
  }
}
