class ChatMessage {
  final String role; // 'user' | 'assistant'
  final String content;
  final bool streaming;
  final String? pendingApprovalId;
  final String? pendingApprovalSkill;
  const ChatMessage({
    required this.role,
    required this.content,
    this.streaming = false,
    this.pendingApprovalId,
    this.pendingApprovalSkill,
  });

  ChatMessage copyWith({String? content, bool? streaming}) => ChatMessage(
        role: role,
        content: content ?? this.content,
        streaming: streaming ?? this.streaming,
        pendingApprovalId: pendingApprovalId,
        pendingApprovalSkill: pendingApprovalSkill,
      );

  /// Returns a copy with approval fields cleared (prevents double-tap).
  ChatMessage clearApproval() => ChatMessage(
        role: role,
        content: content,
        streaming: streaming,
        // pendingApprovalId and pendingApprovalSkill intentionally omitted → null
      );
}
