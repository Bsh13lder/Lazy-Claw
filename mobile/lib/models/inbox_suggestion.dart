/// Immutable model for AI-suggested project/task assignment from
/// `POST /api/budgets/inbox/suggestions`.
class InboxSuggestion {
  final String expenseId;
  final String? projectId;
  final String? projectName;
  final String confidence;
  final String? reason;

  const InboxSuggestion({
    required this.expenseId,
    this.projectId,
    this.projectName,
    required this.confidence,
    this.reason,
  });

  factory InboxSuggestion.fromJson(Map<String, dynamic> json) {
    return InboxSuggestion(
      expenseId: _str(json['expense_id']) ?? '',
      projectId: _str(json['project_id']),
      projectName: _str(json['project_name']),
      confidence: _str(json['confidence']) ?? 'low',
      reason: _str(json['reason']),
    );
  }

  Map<String, dynamic> toJson() => {
        'expense_id': expenseId,
        'project_id': projectId,
        'project_name': projectName,
        'confidence': confidence,
        'reason': reason,
      };

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is InboxSuggestion &&
          other.expenseId == expenseId &&
          other.projectId == projectId &&
          other.projectName == projectName &&
          other.confidence == confidence &&
          other.reason == reason;

  @override
  int get hashCode =>
      expenseId.hashCode ^
      projectId.hashCode ^
      projectName.hashCode ^
      confidence.hashCode ^
      reason.hashCode;

  @override
  String toString() =>
      'InboxSuggestion(expenseId: $expenseId, projectName: $projectName, confidence: $confidence)';
}

// ── Private helpers ────────────────────────────────────────────────────────

String? _str(dynamic v) {
  if (v == null) return null;
  return v.toString();
}
