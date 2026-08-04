/// Pure money math + validation behind the task detail sheet's BUDGET control.
///
/// SCOPE — a task allocation is NOT a ledger. `tasks.allocated_budget` is a
/// plaintext REAL column: "a per-task slice of the parent project's budget".
/// A TASK top-up is therefore a direct adjustment of that single number
/// (`allocated += X`) — it needs no schema change, no backend change and no
/// new sync entity, and it deliberately writes NO `budget_entries` row.
/// The credit ledger (`budget_entries` / [BudgetEntry] / `budget_log_sheet`)
/// is the PROJECT-level concept and stays that way; nothing here should ever
/// be mistaken for, or grown into, a task-level ledger.
///
/// Extracted out of `task_budget_control.dart` (which would otherwise be past
/// this project's file-size guidance) so every rule below is asserted directly
/// rather than through a widget tree — see `test/screens/tasks/
/// task_budget_math_test.dart`. `task_budget_control.dart` re-exports this
/// library, so existing `import '.../task_budget_control.dart'` call sites keep
/// resolving unchanged.
library;

import '../expenses/money_helpers.dart' show fmtMoney;

// ── Bounds & messages ─────────────────────────────────────────────────────────

/// The largest allocation the control will accept.
///
/// `allocated_budget` is a SQLite REAL, so the storage layer would happily take
/// 1e308 — but past a trillion the figure is certainly a typo or a pasted
/// account number, and letting it through silently poisons every rollup that
/// sums against it. Rejecting at the boundary is cheaper than explaining a
/// budget bar that reads "0.0000001% used".
const double kTaskAllocationMax = 1e12;

/// How many decimal places money keeps. Sums of doubles otherwise leak
/// `0.30000000000000004` into the field — and from there into the DB.
const int kMoneyDecimals = 2;

const String kTaskTopUpEmptyError = 'Enter an amount to add';
const String kTaskTopUpInvalidError = 'Enter a valid number';
const String kTaskTopUpNonPositiveError = 'A top-up must be more than 0';
const String kTaskTopUpOverflowError = 'That is too large to allocate';

const String kTaskAllocationInvalidError = kTaskTopUpInvalidError;
const String kTaskAllocationNegativeError = 'An allocation cannot be negative';
const String kTaskAllocationOverflowError = kTaskTopUpOverflowError;

// ── Readout ───────────────────────────────────────────────────────────────────

/// The readout beside the dropdown.
///
/// Both figures are shown together on purpose: an allocation without a spend
/// is a promise, and a spend without its allocation is a number with no
/// meaning. Nothing at all reads as an invitation rather than a broken "0".
String taskBudgetSummaryLabel(
  double? allocated,
  double spent,
  String currency,
) {
  if (allocated == null && spent == 0) return 'No budget yet';
  if (allocated == null) return 'Spent ${fmtMoney(currency, spent)}';
  return 'Allocated ${fmtMoney(currency, allocated)} · '
      'Spent ${fmtMoney(currency, spent)}';
}

/// Whether [spent] has passed [allocated]. Strictly greater — landing exactly
/// on the allocation is on budget, not over it. Always false without an
/// allocation: there is no line to cross.
bool taskBudgetOverspent(double? allocated, double spent) =>
    allocated != null && spent > allocated;

// ── Field text ────────────────────────────────────────────────────────────────

/// Round to cents. The only rounding in this file, so a top-up total and the
/// text written into the allocation field can never disagree.
double roundMoney(double v) {
  final scale = _pow10(kMoneyDecimals);
  return (v * scale).roundToDouble() / scale;
}

double _pow10(int n) {
  var out = 1.0;
  for (var i = 0; i < n; i++) {
    out *= 10;
  }
  return out;
}

/// Format an allocation for the numeric field: a whole number drops the
/// trailing `.0` (`250.0` → `250`), anything else keeps its cents.
///
/// The output MUST round-trip through `double.parse` — the field's text is
/// what the save path parses back into the patch (see `task_detail_patch.dart`),
/// so a "prettier" format with a currency symbol or a thousands separator would
/// silently turn a real edit into a no-op.
String formatTaskAllocation(double v) {
  final rounded = roundMoney(v);
  if (rounded == rounded.roundToDouble()) return rounded.toInt().toString();
  return rounded.toString();
}

/// Re-derive the displayed allocation from what is currently typed in the
/// allocation field.
///
/// An EMPTY field is meaningful — it is the user clearing the allocation — so
/// it resolves to null and the readout says so. Anything that doesn't parse is
/// treated as a transient keystroke (the `3.` on the way to `3.5`, a stray
/// minus) and keeps [previous]: flickering the readout to "No budget yet"
/// mid-word reads as data loss.
double? nextDraftAllocation(String text, double? previous) {
  final trimmed = text.trim();
  if (trimmed.isEmpty) return null;
  final parsed = double.tryParse(trimmed);
  if (parsed == null || !parsed.isFinite || parsed < 0) return previous;
  return parsed;
}

/// Validate the allocation field. Returns null when it is fine to save.
///
/// An empty field is VALID: it is the clear sentinel the save path relies on
/// (see `buildTaskDetailPatch`). Everything else must be a finite, non-negative
/// number within [kTaskAllocationMax] — junk used to be silently dropped at
/// save time, which looked exactly like the edit having been saved.
String? taskAllocationError(String text) {
  final trimmed = text.trim();
  if (trimmed.isEmpty) return null;
  final parsed = double.tryParse(trimmed);
  if (parsed == null || !parsed.isFinite) return kTaskAllocationInvalidError;
  if (parsed < 0) return kTaskAllocationNegativeError;
  if (parsed > kTaskAllocationMax) return kTaskAllocationOverflowError;
  return null;
}

// ── Top up ────────────────────────────────────────────────────────────────────

/// The result of validating a typed top-up against the current allocation:
/// exactly one of [total] (valid — this is the new allocation) and [error]
/// (rejected — nothing may be written) is non-null.
///
/// Immutable by construction so a preview can be recomputed on every keystroke
/// without anyone holding a stale, half-mutated copy.
class TaskTopUpPreview {
  const TaskTopUpPreview.valid(double this.total) : error = null;
  const TaskTopUpPreview.invalid(String this.error) : total = null;

  /// The allocation the task would have AFTER the top-up. Null when invalid.
  final double? total;

  /// Why the amount was refused, for the field's `errorText`. Null when valid.
  final String? error;
}

/// Validate a typed top-up [raw] against [allocated] and compute the new total.
///
/// A top-up only ever ADDS, so zero and negatives are refused rather than
/// quietly reinterpreted as "set to". On a task with NO allocation the top-up
/// DEFINES one (0 + X) — the alternative, refusing, would make the action dead
/// exactly where a user is most likely to reach for it.
TaskTopUpPreview previewTaskTopUp(String raw, double? allocated) {
  final trimmed = raw.trim();
  if (trimmed.isEmpty) return const TaskTopUpPreview.invalid(kTaskTopUpEmptyError);
  final amount = double.tryParse(trimmed);
  // `double.tryParse` happily returns Infinity for '1e400' and NaN for 'NaN';
  // both would sail past the comparisons below, so screen them here.
  if (amount == null || !amount.isFinite) {
    return const TaskTopUpPreview.invalid(kTaskTopUpInvalidError);
  }
  if (amount <= 0) {
    return const TaskTopUpPreview.invalid(kTaskTopUpNonPositiveError);
  }
  final total = roundMoney((allocated ?? 0) + amount);
  if (!total.isFinite || total > kTaskAllocationMax) {
    return const TaskTopUpPreview.invalid(kTaskTopUpOverflowError);
  }
  return TaskTopUpPreview.valid(total);
}

/// The line under the top-up field. Leads with the NEW TOTAL because that is
/// the number the user is deciding about; the old one trails as context so a
/// mis-typed digit is obvious before it is committed.
///
/// [total] null = nothing valid typed yet, so it falls back to the baseline.
String taskTopUpPreviewLabel(double? allocated, double? total, String currency) {
  if (total == null) {
    return allocated == null
        ? 'No allocation yet'
        : 'Allocated ${fmtMoney(currency, allocated)}';
  }
  final next = 'New total ${fmtMoney(currency, total)}';
  if (allocated == null) return next;
  return '$next (was ${fmtMoney(currency, allocated)})';
}
