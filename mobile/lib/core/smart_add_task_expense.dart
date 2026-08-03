/// Amount detection for the ADD-TASK title field — a COMPOSITION of the two
/// existing parsers, deliberately NOT a new matcher inside either of them.
///
/// The task field already parses dates, times, priorities, recurrences and
/// `#`/`/` projects ([parseSmartAdd]). Every one of those families is
/// digit-bearing, and this project has already burned 13 smart-add false
/// positives plus a `mar/march/may` digit-collision bug (772d34d, 6c3ad85) by
/// letting matchers compete for the same characters. So instead of teaching
/// the task grammar about money — which would put a number-hungry matcher in
/// direct competition with `17:00`, `9:30am`, `in 2h`, `every 2 weeks`,
/// `+3d`, `6/10`, `!p1` and `2026-06-30` — this module runs the task parser
/// FIRST and lets the money parser see only what the task parser did not
/// claim.
///
/// The mechanism is the same masking trick [parseSmartExpense] already uses
/// internally to keep its own amount and project matchers apart: every
/// accepted task-token span is replaced by spaces of identical length in a
/// throwaway working copy, so
///   * character offsets still index the ORIGINAL string (spans stay usable
///     for in-field highlighting and for [titleWithoutExpenseToken]), and
///   * a digit that belongs to a date/time/recurrence token is literally not
///     present when [parseSmartExpense] runs.
///
/// Masking can only ever ADD whitespace, so it can neither create a new digit
/// run nor bridge two existing ones — the guard is safe by construction, not
/// by enumeration of known-bad inputs.
///
/// Pure Dart (no I/O, no async, no Flutter), never throws: an unrecognized or
/// fully-consumed line simply yields null.
library;

import 'smart_add_expense_parser.dart';
import 'smart_add_parser.dart';

/// A money amount recognized in a task title, plus its span in the ORIGINAL
/// input so the field can highlight it and [titleWithoutExpenseToken] can
/// remove it.
class TaskExpenseMatch {
  const TaskExpenseMatch({
    required this.amount,
    required this.token,
    this.currency,
  });

  /// The parsed value. Always > 0 — see [detectTaskExpense]'s guard.
  final double amount;

  /// ISO currency code (`EUR`/`USD`/`GBP`/`JPY`) when the text carried an
  /// explicit symbol or code, else null. Resolved by [parseSmartExpense] —
  /// this module never decides what counts as a currency signal itself, so
  /// the two surfaces can't drift.
  final String? currency;

  /// The `[start, end)` span of the amount token in the original input,
  /// always [SmartTokenKind.amount].
  final SmartToken token;

  /// Whether the user typed an EXPLICIT currency marker (`€`, `$`, `£`,
  /// `eur`, `usd`, …). This — and only this — is what callers use to arm an
  /// expense by default: a bare number ("buy 2 apples") is far too weak a
  /// signal to spend money on, while a false negative costs one tap.
  bool get hasCurrencyMarker => currency != null;
}

/// The most credible money amount left in [input] after [parsed]'s tokens are
/// masked out, or null when nothing survives.
///
/// [parsed] MUST be the result of `parseSmartAdd(input)` — the caller already
/// has it, and passing it in avoids a second parse per keystroke.
///
/// Candidate preference: the FIRST candidate carrying an explicit currency
/// marker wins; only when no candidate has one does the first bare number
/// stand in. Without that, "buy 3 tickets $60" would file a 3-unit expense and
/// silently drop the $60 the user actually typed.
TaskExpenseMatch? detectTaskExpense(ParsedTask parsed, String input) {
  var work = _maskSpans(input, parsed.tokens);
  TaskExpenseMatch? firstBare;

  // Each pass masks the candidate it just examined, so `work` strictly loses
  // at least one non-space character per iteration — the loop is bounded by
  // the input length even before the explicit cap below.
  for (var pass = 0; pass < _maxCandidates; pass++) {
    final expense = parseSmartExpense(work);
    final amount = expense.amount;
    final token = _trimTrailingSpace(_amountTokenOf(expense), work);
    if (amount == null || token == null) break;
    // A zero (or, defensively, negative) amount is not a purchase — "buy 0
    // sugar" must not offer to file a 0-value expense row.
    if (amount > 0) {
      final match = TaskExpenseMatch(
        amount: amount,
        currency: expense.currency,
        token: token,
      );
      if (match.hasCurrencyMarker) return match;
      firstBare ??= match;
    }
    work = _maskSpans(work, [token]);
  }
  return firstBare;
}

/// The task title with [parsed]'s tokens AND the armed [amountToken] removed.
///
/// Mirrors [parseSmartAdd]'s own clean-title construction exactly (remove the
/// accepted spans right-to-left so an earlier removal can't shift a later
/// span's indices, then collapse whitespace) rather than inventing a second
/// stripping rule — a consumed token is a stripped token here, same as every
/// other token family in this field.
///
/// May return an empty string (e.g. the whole title was "40 eur"); the caller
/// is responsible for its own fall-back, exactly as it already is for
/// [ParsedTask.cleanTitle].
String titleWithoutExpenseToken(
  String input,
  ParsedTask parsed,
  SmartToken amountToken,
) {
  final spans = [...parsed.tokens, amountToken]
    ..sort((a, b) => b.start.compareTo(a.start));
  var stripped = input;
  for (final t in spans) {
    stripped = stripped.replaceRange(t.start, t.end, ' ');
  }
  return stripped.replaceAll(_whitespace, ' ').trim();
}

/// [taskTokens] plus [amountToken], sorted by start.
///
/// `SmartAddController.buildTextSpan` walks its spans with a single forward
/// cursor and SILENTLY DROPS anything out of order, so an unsorted list would
/// lose a highlight with no error. The spans cannot overlap (the amount was
/// found in text where the task tokens were masked away), so a plain sort is
/// sufficient. Returns a new list — [taskTokens] is never mutated.
List<SmartToken> mergeExpenseToken(
  List<SmartToken> taskTokens,
  SmartToken amountToken,
) =>
    [...taskTokens, amountToken]..sort((a, b) => a.start.compareTo(b.start));

// ── internals ────────────────────────────────────────────────────────────────

final RegExp _whitespace = RegExp(r'\s+');

/// Defensive upper bound on candidate passes in [detectTaskExpense]. Progress
/// is already guaranteed by masking, so this only caps the pathological case
/// of a title stuffed with dozens of bare numbers — at which point the user is
/// plainly not typing an expense and the first bare candidate is answer enough.
const int _maxCandidates = 8;

/// Replace each span in [spans] with spaces of the SAME length, so every
/// character offset outside them still indexes the original string.
String _maskSpans(String input, List<SmartToken> spans) {
  var masked = input;
  for (final t in spans) {
    if (t.start < 0 || t.end > masked.length || t.end <= t.start) continue;
    masked = masked.replaceRange(t.start, t.end, ' ' * (t.end - t.start));
  }
  return masked;
}

/// [ParsedExpense.tokens] also carries a `#`/`/` project span when one is
/// present (a SECOND project token the task parser left behind, since it only
/// consumes the first). Only the amount span is ours.
SmartToken? _amountTokenOf(ParsedExpense expense) {
  for (final t in expense.tokens) {
    if (t.kind == SmartTokenKind.amount) return t;
  }
  return null;
}

/// Shrink [token] back off any trailing whitespace in [work].
///
/// The expense parser's amount pattern ends `\s*(EUR|USD|…)?(?=\s|$)`, so when
/// the optional trailing currency group matches NOTHING the `\s*` still eats
/// every space up to the boundary — and after masking, a whole consumed task
/// token is spaces. Untrimmed, "coffee $12.50 today" yields an amount span
/// covering `$12.50 today`'s masked tail, which would (a) paint the highlight
/// over text that isn't money and (b) OVERLAP the date token in
/// [mergeExpenseToken], whose non-overlap guarantee
/// `SmartAddController.buildTextSpan` relies on to render at all (it silently
/// drops any span starting before the previous one ended — the date highlight
/// would just disappear). Trimming restores "the span is exactly the money the
/// user typed".
SmartToken? _trimTrailingSpace(SmartToken? token, String work) {
  if (token == null) return null;
  var end = token.end.clamp(0, work.length);
  while (end > token.start && _isSpace(work.codeUnitAt(end - 1))) {
    end--;
  }
  if (end <= token.start) return null; // defensive: never emit an empty span
  return end == token.end
      ? token
      : SmartToken(token.start, end, token.kind);
}

/// Space / tab / newline — the characters `_maskSpans` and the parser's own
/// `\s` class produce. Avoids building a RegExp per character.
bool _isSpace(int codeUnit) =>
    codeUnit == 0x20 || codeUnit == 0x09 || codeUnit == 0x0A || codeUnit == 0x0D;
