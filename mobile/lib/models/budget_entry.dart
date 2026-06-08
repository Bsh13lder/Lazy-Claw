/// Immutable model mirroring a row of the `budget_entries` ledger
/// (`GET /api/budgets/projects/{id}/budget-entries`).
///
/// A ledger entry records a change to a project's total budget and WHERE the
/// money came from. Two kinds exist:
///   * `credit` — a sourced top-up ("+ Add to budget"); always bumps the total.
///   * `edit`   — an audit row written when the project total was set directly;
///                its [amount] is the delta and can be negative (a debit).
///
/// All fields are nullable-tolerant: the server may omit optional fields or send
/// numbers as different numeric types.
class BudgetEntry {
  final String id;
  final String projectId;

  /// Signed delta applied to the project budget. Positive for top-ups, can be
  /// negative for a downward edit.
  final double amount;
  final String currency;

  /// Free-text note — the answer to "where is this budget from?" (or the
  /// human-readable "edit: X → Y" string on an audit row). Null when unset.
  final String? source;

  /// `"credit"` (a top-up) or `"edit"` (a direct-set audit row).
  final String kind;

  /// ISO-8601 timestamp the entry was written, or null on a malformed row.
  final String? createdAt;

  const BudgetEntry({
    required this.id,
    required this.projectId,
    required this.amount,
    required this.currency,
    this.source,
    this.kind = 'credit',
    this.createdAt,
  });

  /// True when this row is a direct-set audit ("Budget edited") rather than a
  /// sourced top-up ("Budget added"). The web renders these differently.
  bool get isEdit => kind == 'edit';

  /// The date portion (`YYYY-MM-DD`) of [createdAt], or empty when absent.
  String get date {
    final ts = createdAt;
    if (ts == null || ts.length < 10) return '';
    return ts.substring(0, 10);
  }

  factory BudgetEntry.fromJson(Map<String, dynamic> json) {
    return BudgetEntry(
      id: _str(json['id']) ?? '',
      projectId: _str(json['project_id']) ?? '',
      amount: _double(json['amount']) ?? 0.0,
      currency: _str(json['currency']) ?? 'USD',
      source: _str(json['source']),
      kind: _str(json['kind']) ?? 'credit',
      createdAt: _str(json['created_at']),
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'project_id': projectId,
        'amount': amount,
        'currency': currency,
        'source': source,
        'kind': kind,
        'created_at': createdAt,
      };

  @override
  bool operator ==(Object other) =>
      identical(this, other) || other is BudgetEntry && other.id == id;

  @override
  int get hashCode => id.hashCode;

  @override
  String toString() =>
      'BudgetEntry(id: $id, amount: $amount, kind: $kind)';
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
