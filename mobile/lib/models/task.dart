import 'subtask.dart';

/// Immutable model mirroring the /api/tasks TaskItem shape from api.ts.
/// All fields are nullable-tolerant: the server may omit optional fields or
/// send integers instead of strings for legacy builds.
class Task {
  final String id;
  final String userId;
  final String title;
  final String? description;
  final String? category;
  final String priority;
  final String status;
  final String owner;
  final String? dueDate;
  final String? reminderAt;
  final String? recurring;
  final String? tags;
  final int nagCount;
  final String createdAt;
  final String? completedAt;
  final String? lastError;
  final int? attemptCount;
  final String? lastAttemptedAt;
  final String? traceSessionId;
  final String? lazybrainNoteId;
  final String? steps;
  final double? allocatedBudget;

  const Task({
    required this.id,
    required this.userId,
    required this.title,
    this.description,
    this.category,
    required this.priority,
    required this.status,
    required this.owner,
    this.dueDate,
    this.reminderAt,
    this.recurring,
    this.tags,
    required this.nagCount,
    required this.createdAt,
    this.completedAt,
    this.lastError,
    this.attemptCount,
    this.lastAttemptedAt,
    this.traceSessionId,
    this.lazybrainNoteId,
    this.steps,
    this.allocatedBudget,
  });

  bool get isDone => status == 'done';

  /// The sub-tasks (checklist items) parsed from the `steps` JSON column.
  /// Empty when there are none. See [Subtask] for the storage shape.
  List<Subtask> get subtasks => parseSubtasks(steps);

  factory Task.fromJson(Map<String, dynamic> json) {
    return Task(
      id: _str(json['id']) ?? '',
      userId: _str(json['user_id']) ?? '',
      title: _str(json['title']) ?? '',
      description: _str(json['description']),
      category: _str(json['category']),
      priority: _str(json['priority']) ?? 'medium',
      status: _str(json['status']) ?? 'todo',
      owner: _str(json['owner']) ?? 'user',
      dueDate: _str(json['due_date']),
      reminderAt: _str(json['reminder_at']),
      recurring: _str(json['recurring']),
      tags: _str(json['tags']),
      nagCount: _int(json['nag_count']) ?? 0,
      createdAt: _str(json['created_at']) ?? '',
      completedAt: _str(json['completed_at']),
      lastError: _str(json['last_error']),
      attemptCount: _int(json['attempt_count']),
      lastAttemptedAt: _str(json['last_attempted_at']),
      traceSessionId: _str(json['trace_session_id']),
      lazybrainNoteId: _str(json['lazybrain_note_id']),
      steps: _str(json['steps']),
      allocatedBudget: _double(json['allocated_budget']),
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'user_id': userId,
        'title': title,
        'description': description,
        'category': category,
        'priority': priority,
        'status': status,
        'owner': owner,
        'due_date': dueDate,
        'reminder_at': reminderAt,
        'recurring': recurring,
        'tags': tags,
        'nag_count': nagCount,
        'created_at': createdAt,
        'completed_at': completedAt,
        'last_error': lastError,
        'attempt_count': attemptCount,
        'last_attempted_at': lastAttemptedAt,
        'trace_session_id': traceSessionId,
        'lazybrain_note_id': lazybrainNoteId,
        'steps': steps,
        'allocated_budget': allocatedBudget,
      };

  /// Returns a copy with the given fields replaced. A null argument means
  /// "unchanged" for every field — so to CLEAR the (nullable) due date, pass
  /// [clearDueDate]`: true` (a plain `dueDate: null` can't be distinguished from
  /// "leave it alone"). Mirrors how the DAO's `''` clear sentinel is realized.
  Task copyWith({
    String? id,
    String? userId,
    String? title,
    String? description,
    String? category,
    String? priority,
    String? status,
    String? owner,
    String? dueDate,
    bool clearDueDate = false,
    String? reminderAt,
    String? recurring,
    String? tags,
    int? nagCount,
    String? createdAt,
    String? completedAt,
    String? lastError,
    int? attemptCount,
    String? lastAttemptedAt,
    String? traceSessionId,
    String? lazybrainNoteId,
    String? steps,
    double? allocatedBudget,
  }) =>
      Task(
        id: id ?? this.id,
        userId: userId ?? this.userId,
        title: title ?? this.title,
        description: description ?? this.description,
        category: category ?? this.category,
        priority: priority ?? this.priority,
        status: status ?? this.status,
        owner: owner ?? this.owner,
        dueDate: clearDueDate ? null : (dueDate ?? this.dueDate),
        reminderAt: reminderAt ?? this.reminderAt,
        recurring: recurring ?? this.recurring,
        tags: tags ?? this.tags,
        nagCount: nagCount ?? this.nagCount,
        createdAt: createdAt ?? this.createdAt,
        completedAt: completedAt ?? this.completedAt,
        lastError: lastError ?? this.lastError,
        attemptCount: attemptCount ?? this.attemptCount,
        lastAttemptedAt: lastAttemptedAt ?? this.lastAttemptedAt,
        traceSessionId: traceSessionId ?? this.traceSessionId,
        lazybrainNoteId: lazybrainNoteId ?? this.lazybrainNoteId,
        steps: steps ?? this.steps,
        allocatedBudget: allocatedBudget ?? this.allocatedBudget,
      );

  @override
  bool operator ==(Object other) =>
      identical(this, other) || other is Task && other.id == id;

  @override
  int get hashCode => id.hashCode;
}

// ── Private helpers ────────────────────────────────────────────────────────

String? _str(dynamic v) {
  if (v == null) return null;
  return v.toString();
}

int? _int(dynamic v) {
  if (v == null) return null;
  if (v is int) return v;
  if (v is double) return v.toInt();
  return int.tryParse(v.toString());
}

double? _double(dynamic v) {
  if (v == null) return null;
  if (v is double) return v;
  if (v is int) return v.toDouble();
  return double.tryParse(v.toString());
}
