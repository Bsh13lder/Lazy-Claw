/// Immutable model mirroring the /api/budgets/expenses Expense shape.
/// All fields are nullable-tolerant: the server may omit optional fields
/// or send numbers as different numeric types.
class Expense {
  final String id;
  final String projectId;
  final String? taskId;
  final double amount;
  final String currency;
  final String? description;
  final String? vendor;
  final String? notes;
  final String? spentAt;
  final String status;
  final String? recurringExpenseId;
  final String? lazybrainNoteId;

  /// Decrypted project name — present on the cross-project ledger only.
  final String? projectName;

  const Expense({
    required this.id,
    required this.projectId,
    this.taskId,
    required this.amount,
    required this.currency,
    this.description,
    this.vendor,
    this.notes,
    this.spentAt,
    required this.status,
    this.recurringExpenseId,
    this.lazybrainNoteId,
    this.projectName,
  });

  bool get isVoid => status == 'void';

  /// Returns `description` if non-null, otherwise falls back to `vendor`,
  /// then empty string — safe for display in list tiles.
  String get displayDescription =>
      description?.isNotEmpty == true
          ? description!
          : vendor ?? '';

  factory Expense.fromJson(Map<String, dynamic> json) {
    return Expense(
      id: _str(json['id']) ?? '',
      projectId: _str(json['project_id']) ?? '',
      taskId: _str(json['task_id']),
      amount: _double(json['amount']) ?? 0.0,
      currency: _str(json['currency']) ?? 'USD',
      description: _str(json['description']),
      vendor: _str(json['vendor']),
      notes: _str(json['notes']),
      spentAt: _str(json['spent_at']),
      status: _str(json['status']) ?? 'posted',
      recurringExpenseId: _str(json['recurring_expense_id']),
      lazybrainNoteId: _str(json['lazybrain_note_id']),
      projectName: _str(json['project_name']),
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'project_id': projectId,
        'task_id': taskId,
        'amount': amount,
        'currency': currency,
        'description': description,
        'vendor': vendor,
        'notes': notes,
        'spent_at': spentAt,
        'status': status,
        'recurring_expense_id': recurringExpenseId,
        'lazybrain_note_id': lazybrainNoteId,
        'project_name': projectName,
      };

  Expense copyWith({
    String? id,
    String? projectId,
    String? taskId,
    double? amount,
    String? currency,
    String? description,
    String? vendor,
    String? notes,
    String? spentAt,
    String? status,
    String? recurringExpenseId,
    String? lazybrainNoteId,
    String? projectName,
  }) =>
      Expense(
        id: id ?? this.id,
        projectId: projectId ?? this.projectId,
        taskId: taskId ?? this.taskId,
        amount: amount ?? this.amount,
        currency: currency ?? this.currency,
        description: description ?? this.description,
        vendor: vendor ?? this.vendor,
        notes: notes ?? this.notes,
        spentAt: spentAt ?? this.spentAt,
        status: status ?? this.status,
        recurringExpenseId: recurringExpenseId ?? this.recurringExpenseId,
        lazybrainNoteId: lazybrainNoteId ?? this.lazybrainNoteId,
        projectName: projectName ?? this.projectName,
      );

  @override
  bool operator ==(Object other) =>
      identical(this, other) || other is Expense && other.id == id;

  @override
  int get hashCode => id.hashCode;

  @override
  String toString() => 'Expense(id: $id, amount: $amount, description: $description)';
}

// ── Private helpers ────────────────────────────────────────────────────────

String? _str(dynamic v) {
  if (v == null) return null;
  return v.toString();
}

double? _double(dynamic v) {
  if (v == null) return null;
  if (v is double) return v;
  if (v is int) return v.toDouble();
  return double.tryParse(v.toString());
}
