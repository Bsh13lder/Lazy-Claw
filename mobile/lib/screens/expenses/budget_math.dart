/// Pure budget math for the Money screen — derives spend/remaining/totals from
/// the actual cached expense set instead of trusting a (possibly stale or
/// absent) server rollup. Kept dependency-free so it is trivially unit-testable.
library;

import '../../models/expense.dart';
import '../../models/project.dart';

/// Sum of the [expenses] that belong to [projectId] and are live (not void).
/// Callers pass the already non-deleted set (the DAO filters tombstones), but we
/// still guard `isVoid` so a voided row never inflates a project's spend.
double spentForProject(String projectId, Iterable<Expense> expenses) {
  var total = 0.0;
  for (final e in expenses) {
    if (e.projectId == projectId && !e.isVoid) total += e.amount;
  }
  return total;
}

/// Remaining budget for a project given its derived [spent]. May go negative
/// (over budget) — callers decide how to present that.
double remainingForProject(Project project, double spent) =>
    project.budget - spent;

/// Aggregate figures for the Money overview hero card, computed from the live
/// expense + project sets.
///
/// Amounts in different currencies are NOT summed together (that would be
/// meaningless). Instead a single dominant currency is chosen and the totals are
/// scoped to it; [multiCurrency] / [otherCurrencyCount] let the UI disclose that
/// other-currency spend exists but is shown separately.
class BudgetTotals {
  /// The currency the headline figures are denominated in.
  final String currency;

  /// Sum of budgets across projects in [currency].
  final double totalBudget;

  /// Sum of live (non-void) expense amounts in [currency] — the true total
  /// spend, including expenses whose project was deleted locally (orphans).
  final double totalSpent;

  /// Subset of [totalSpent] whose `spent_at` falls in the current calendar
  /// month (local time).
  final double monthSpent;

  /// True when projects/expenses span more than one currency.
  final bool multiCurrency;

  /// How many currencies other than [currency] are present.
  final int otherCurrencyCount;

  const BudgetTotals({
    required this.currency,
    required this.totalBudget,
    required this.totalSpent,
    required this.monthSpent,
    required this.multiCurrency,
    required this.otherCurrencyCount,
  });

  static const empty = BudgetTotals(
    currency: 'USD',
    totalBudget: 0,
    totalSpent: 0,
    monthSpent: 0,
    multiCurrency: false,
    otherCurrencyCount: 0,
  );

  double get remaining => totalBudget - totalSpent;

  /// Fraction of budget spent, clamped to [0, 1]. 0 when no budget is set.
  double get fraction =>
      totalBudget > 0 ? (totalSpent / totalBudget).clamp(0.0, 1.0) : 0.0;

  bool get hasBudget => totalBudget > 0;

  bool get overBudget => totalBudget > 0 && totalSpent > totalBudget;

  /// Whole-percent of the budget used (0 when no budget set).
  int get percentUsed => totalBudget > 0 ? (fraction * 100).round() : 0;

  /// Build the overview totals from the live [projects] + [expenses].
  ///
  /// [now] defaults to [DateTime.now] and is injectable for deterministic
  /// month-boundary tests.
  factory BudgetTotals.from(
    List<Project> projects,
    List<Expense> expenses, {
    DateTime? now,
  }) {
    final live = expenses.where((e) => !e.isVoid).toList();
    if (projects.isEmpty && live.isEmpty) return BudgetTotals.empty;

    // Tally how prevalent each currency is. Projects weigh more than loose
    // expenses when choosing the headline currency (the budget lives there).
    final weight = <String, int>{};
    for (final p in projects) {
      weight[p.currency] = (weight[p.currency] ?? 0) + 2;
    }
    for (final e in live) {
      weight[e.currency] = (weight[e.currency] ?? 0) + 1;
    }

    final currency = _dominant(weight) ?? 'USD';
    final distinctCurrencies = weight.keys.toSet();

    var totalBudget = 0.0;
    for (final p in projects) {
      if (p.currency == currency) totalBudget += p.budget;
    }

    final ref = now ?? DateTime.now();
    var totalSpent = 0.0;
    var monthSpent = 0.0;
    for (final e in live) {
      if (e.currency != currency) continue;
      totalSpent += e.amount;
      if (_isInMonth(e.spentAt, ref)) monthSpent += e.amount;
    }

    return BudgetTotals(
      currency: currency,
      totalBudget: totalBudget,
      totalSpent: totalSpent,
      monthSpent: monthSpent,
      multiCurrency: distinctCurrencies.length > 1,
      otherCurrencyCount:
          distinctCurrencies.isEmpty ? 0 : distinctCurrencies.length - 1,
    );
  }

  /// The highest-weighted currency, deterministic on ties (alphabetical) so the
  /// headline doesn't flicker between equally-common currencies.
  static String? _dominant(Map<String, int> weight) {
    String? best;
    int bestWeight = -1;
    for (final entry in weight.entries) {
      if (entry.value > bestWeight ||
          (entry.value == bestWeight &&
              (best == null || entry.key.compareTo(best) < 0))) {
        best = entry.key;
        bestWeight = entry.value;
      }
    }
    return best;
  }

  /// True when an ISO-8601 [spentAt] (date or datetime) is in [ref]'s month.
  static bool _isInMonth(String? spentAt, DateTime ref) {
    if (spentAt == null || spentAt.length < 7) return false;
    final d = DateTime.tryParse(spentAt);
    if (d == null) return false;
    return d.year == ref.year && d.month == ref.month;
  }
}
