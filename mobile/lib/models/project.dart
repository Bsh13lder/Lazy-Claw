import 'package:flutter/material.dart';

/// Immutable model mirroring the /api/budgets/projects Project shape.
/// All fields are nullable-tolerant: the server may omit optional fields
/// or send numbers as different numeric types.
class Project {
  final String id;
  final String name;
  final String? nameKey;
  final double budget;
  final String currency;
  final String status;
  final String? description;
  final String? lazybrainNoteId;

  /// User-chosen accent for the project, as a `"#RRGGBB"` hex string (or null
  /// when no color has been picked). Round-trips through the budgets offline
  /// sync alongside the rest of the project fields.
  final String? color;

  /// Whether the user pinned this project as a favorite. Favorited projects
  /// surface in the Home "Favorites" section while the full list stays in the
  /// Expenses tab. Round-trips through the budgets offline sync.
  final bool isFavorite;

  /// The project's time frame, as plaintext `yyyy-MM-dd` strings (or null when
  /// unset). An `''` riding an update is the clear sentinel — the server
  /// leniently nulls empty/invalid values. Round-trips through the budgets
  /// offline sync alongside the rest of the project fields.
  final String? startDate;
  final String? dueDate;

  /// Rolled-up totals — present on list/report responses, null otherwise.
  final double? spent;
  final double? remaining;

  const Project({
    required this.id,
    required this.name,
    this.nameKey,
    required this.budget,
    required this.currency,
    required this.status,
    this.description,
    this.lazybrainNoteId,
    this.color,
    this.isFavorite = false,
    this.startDate,
    this.dueDate,
    this.spent,
    this.remaining,
  });

  /// Fraction of budget spent, clamped to [0, 1]. Returns 0 when budget is 0
  /// or when `spent` is null.
  double get spentFraction {
    if (budget <= 0) return 0.0;
    final s = spent ?? 0.0;
    return (s / budget).clamp(0.0, 1.0);
  }

  bool get isArchived => status == 'archived';

  /// Whether this is the auto-created "Inbox" project that uncategorized
  /// expenses land in. Keyed on `nameKey` (the stable, locale-independent
  /// slug), falling back to a case-insensitive name match for legacy cached
  /// rows written before `nameKey` existed.
  bool get isInbox => (nameKey ?? name.toLowerCase()) == 'general';

  /// Traffic-light color for the budget bar:
  ///   <70 % → green, 70–90 % → amber, >90 % → red.
  Color get budgetBarColor {
    final f = spentFraction;
    if (f < 0.70) return Colors.green.shade600;
    if (f < 0.90) return Colors.amber.shade700;
    return Colors.red.shade600;
  }

  factory Project.fromJson(Map<String, dynamic> json) {
    return Project(
      id: _str(json['id']) ?? '',
      name: _str(json['name']) ?? '',
      nameKey: _str(json['name_key']),
      budget: _double(json['budget']) ?? 0.0,
      currency: _str(json['currency']) ?? 'USD',
      status: _str(json['status']) ?? 'active',
      description: _str(json['description']),
      lazybrainNoteId: _str(json['lazybrain_note_id']),
      color: _str(json['color']),
      isFavorite: _bool(json['is_favorite']),
      startDate: _str(json['start_date']),
      dueDate: _str(json['due_date']),
      spent: _double(json['spent']),
      remaining: _double(json['remaining']),
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'name_key': nameKey,
        'budget': budget,
        'currency': currency,
        'status': status,
        'description': description,
        'lazybrain_note_id': lazybrainNoteId,
        'color': color,
        'is_favorite': isFavorite,
        'start_date': startDate,
        'due_date': dueDate,
        'spent': spent,
        'remaining': remaining,
      };

  Project copyWith({
    String? id,
    String? name,
    String? nameKey,
    double? budget,
    String? currency,
    String? status,
    String? description,
    String? lazybrainNoteId,
    String? color,
    bool? isFavorite,
    String? startDate,
    String? dueDate,
    double? spent,
    double? remaining,
  }) =>
      Project(
        id: id ?? this.id,
        name: name ?? this.name,
        nameKey: nameKey ?? this.nameKey,
        budget: budget ?? this.budget,
        currency: currency ?? this.currency,
        status: status ?? this.status,
        description: description ?? this.description,
        lazybrainNoteId: lazybrainNoteId ?? this.lazybrainNoteId,
        color: color ?? this.color,
        isFavorite: isFavorite ?? this.isFavorite,
        startDate: startDate ?? this.startDate,
        dueDate: dueDate ?? this.dueDate,
        spent: spent ?? this.spent,
        remaining: remaining ?? this.remaining,
      );

  @override
  bool operator ==(Object other) =>
      identical(this, other) || other is Project && other.id == id;

  @override
  int get hashCode => id.hashCode;

  @override
  String toString() => 'Project(id: $id, name: $name, budget: $budget)';
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
