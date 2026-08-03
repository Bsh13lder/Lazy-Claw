/// Immutable model mirroring the /api/budgets/expenses Expense shape.
/// All fields are nullable-tolerant: the server may omit optional fields
/// or send numbers as different numeric types.
class Expense {
  final String id;
  final String projectId;
  final String? taskId;

  /// The sub-task (checklist item) within [taskId] this expense is pinned to,
  /// or null for a plain task-level (or task-less) expense. Plaintext — no
  /// user content, same as [taskId]. Server invariant: non-null requires
  /// [taskId] to be non-null too (an orphan subtask link is rejected at
  /// write time), and deleting the sub-task DEMOTES this back to null rather
  /// than deleting the expense — the money always survives on the task.
  final String? subtaskId;
  final double amount;
  final String currency;
  final String? description;
  final String? vendor;
  final String? notes;
  final String? spentAt;

  /// When the expense row was recorded (server `created_at`, ISO-8601, usually
  /// UTC). Distinct from [spentAt] (the date the money was spent). Surfaced as a
  /// "saved" caption on the ledger so the user sees when each entry landed.
  final String? createdAt;

  final String status;
  final String? recurringExpenseId;
  final String? lazybrainNoteId;

  /// Decrypted project name — present on the cross-project ledger only.
  final String? projectName;

  /// Whether the user pinned this expense as a favorite. Starred expenses feed
  /// the Money "★ Starred only" subtotal so the user can total just the entries
  /// they care about. Round-trips through the budgets offline sync.
  final bool isFavorite;

  const Expense({
    required this.id,
    required this.projectId,
    this.taskId,
    this.subtaskId,
    required this.amount,
    required this.currency,
    this.description,
    this.vendor,
    this.notes,
    this.spentAt,
    this.createdAt,
    required this.status,
    this.recurringExpenseId,
    this.lazybrainNoteId,
    this.projectName,
    this.isFavorite = false,
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
      subtaskId: _str(json['subtask_id']),
      amount: _double(json['amount']) ?? 0.0,
      currency: _str(json['currency']) ?? 'USD',
      description: _str(json['description']),
      vendor: _str(json['vendor']),
      notes: _str(json['notes']),
      spentAt: _str(json['spent_at']),
      createdAt: _str(json['created_at']),
      status: _str(json['status']) ?? 'posted',
      recurringExpenseId: _str(json['recurring_expense_id']),
      lazybrainNoteId: _str(json['lazybrain_note_id']),
      projectName: _str(json['project_name']),
      isFavorite: _bool(json['is_favorite']),
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'project_id': projectId,
        'task_id': taskId,
        'subtask_id': subtaskId,
        'amount': amount,
        'currency': currency,
        'description': description,
        'vendor': vendor,
        'notes': notes,
        'spent_at': spentAt,
        'created_at': createdAt,
        'status': status,
        'recurring_expense_id': recurringExpenseId,
        'lazybrain_note_id': lazybrainNoteId,
        'project_name': projectName,
        'is_favorite': isFavorite,
      };

  /// [clearTaskId] / [clearSubtaskId] are explicit clear flags: the ordinary
  /// `taskId ?? this.taskId` / `subtaskId ?? this.subtaskId` fallbacks below
  /// mean passing `null` for either is indistinguishable from "leave
  /// unchanged" — there's no other way to actually NULL out an existing link
  /// through this method. A task change must be able to clear a stale
  /// sub-task link (a sub-task belongs to exactly one task), so both flags
  /// are needed, not just one.
  Expense copyWith({
    String? id,
    String? projectId,
    String? taskId,
    bool clearTaskId = false,
    String? subtaskId,
    bool clearSubtaskId = false,
    double? amount,
    String? currency,
    String? description,
    String? vendor,
    String? notes,
    String? spentAt,
    String? createdAt,
    String? status,
    String? recurringExpenseId,
    String? lazybrainNoteId,
    String? projectName,
    bool? isFavorite,
  }) =>
      Expense(
        id: id ?? this.id,
        projectId: projectId ?? this.projectId,
        taskId: clearTaskId ? null : (taskId ?? this.taskId),
        subtaskId: clearSubtaskId ? null : (subtaskId ?? this.subtaskId),
        amount: amount ?? this.amount,
        currency: currency ?? this.currency,
        description: description ?? this.description,
        vendor: vendor ?? this.vendor,
        notes: notes ?? this.notes,
        spentAt: spentAt ?? this.spentAt,
        createdAt: createdAt ?? this.createdAt,
        status: status ?? this.status,
        recurringExpenseId: recurringExpenseId ?? this.recurringExpenseId,
        lazybrainNoteId: lazybrainNoteId ?? this.lazybrainNoteId,
        projectName: projectName ?? this.projectName,
        isFavorite: isFavorite ?? this.isFavorite,
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

/// Coerce a JSON/SQLite favorite flag to a bool. The server sends a JSON bool,
/// the local cache stores an INTEGER 0/1, and a pre-migration row is null —
/// all map cleanly to false unless explicitly truthy.
bool _bool(dynamic v) {
  if (v == null) return false;
  if (v is bool) return v;
  if (v is num) return v != 0;
  final s = v.toString().toLowerCase();
  return s == 'true' || s == '1';
}
