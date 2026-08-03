/// On-device, offline, zero-LLM smart-add parsing for EXPENSE lines.
///
/// A SIBLING to [parseSmartAdd] (`smart_add_parser.dart`), not an extension of
/// it. Expense lines are dominated by digits ("spent on #clubbay 25", "12.90
/// coffee"), which collides badly with the task grammar: a bare `!2` reads as
/// a priority, and a trailing "in 2 days" fights the amount over which number
/// is the money. So this parser recognizes ONLY what an expense line actually
/// needs: an amount (the mandatory anchor), an optional currency riding along
/// with it, and an optional `#`/`/` project — reusing the exact same
/// project-token pattern the task parser uses (`smart_add/project_token.dart`)
/// so `#project` recognition can never drift between the two siblings.
///
/// v1 deliberately loads NO date/time/priority/recurrence matcher — see Task
/// 10 of `docs/superpowers/plans/2026-08-03-sync-widget-parser-expenses.md`
/// for the full rationale and the anti-pattern list this design closes off
/// (`parking !2`, `25,50 groceries`, `6/10 dinner`).
///
/// Pure Dart (no I/O, no async), never throws — an unrecognized amount just
/// leaves [ParsedExpense.amount] null and the description untouched.
library;

import 'smart_add/project_token.dart';
import 'smart_add_parser.dart' show SmartToken, SmartTokenKind;

/// The structured result of parsing a smart-add EXPENSE line.
class ParsedExpense {
  /// The description with the recognized amount/currency/project tokens
  /// removed and whitespace collapsed.
  final String cleanDescription;

  /// The parsed money amount, or null when no amount token matched. Amount is
  /// the mandatory anchor: nothing else about the input is even inspected for
  /// tokens the field itself couldn't need, so a line with no recognizable
  /// number simply parses to an all-null result.
  final double? amount;

  /// The ISO currency code (`EUR`/`USD`/`GBP`/`JPY`) carried by an explicit
  /// symbol or code riding with the amount, or null when the amount had
  /// neither. Callers fall back to their own existing default when null —
  /// exactly the same fallback the manual (non-typed) Add Expense flow
  /// already uses — so quick-typing introduces no NEW currency divergence.
  final String? currency;

  /// Project / category name (from a `#`/`/` token), or null.
  final String? project;

  /// The accepted token spans, sorted by [SmartToken.start], non-overlapping,
  /// each indexing into the *original* input string. Empty when nothing
  /// matched.
  final List<SmartToken> tokens;

  const ParsedExpense({
    required this.cleanDescription,
    this.amount,
    this.currency,
    this.project,
    this.tokens = const [],
  });
}

// `(leading symbol)? digits(.decimals)? (trailing code/symbol)?`, token-
// bounded on both ends — deliberately narrow: at most 6 integer digits, at
// most 2 decimal digits. Only `.` is ever treated as a decimal point: a comma
// directly after the digits (the sentence-comma case, "spent 25, groceries")
// breaks the trailing token-boundary lookahead, so the WHOLE match fails
// rather than misreading `25,50` as `25.50`. Case-insensitive so
// `eur`/`usd`/`gbp`/`jpy` match alongside the upper-case ISO codes.
final RegExp _amountPattern = RegExp(
  r'(^|\s)(€|\$|£)?(\d{1,6}(?:\.\d{1,2})?)\s*(EUR|USD|GBP|JPY|€|\$|£)?(?=\s|$)',
  caseSensitive: false,
);

final RegExp _whitespace = RegExp(r'\s+');

/// Parse [input] into a [ParsedExpense].
///
/// [now] is accepted for interface parity with [parseSmartAdd] (and in case a
/// future `spentAt` date matcher lands — explicitly DEFERRED for v1, see the
/// plan) but is unused today.
ParsedExpense parseSmartExpense(String input, {DateTime? now}) {
  SmartToken? amountToken;
  double? amount;
  String? currency;

  final am = _amountPattern.firstMatch(input);
  if (am != null) {
    final parsedAmount = double.tryParse(am.group(3)!);
    if (parsedAmount != null) {
      final start = am.start + (am.group(1)?.length ?? 0);
      amount = parsedAmount;
      currency = _resolveCurrency(am.group(2), am.group(4));
      amountToken = SmartToken(start, am.end, SmartTokenKind.amount);
    }
  }

  // Mask the accepted amount span (if any) with spaces of the same length in
  // a WORKING copy before running the project matcher, so a `#`/`/` token can
  // never be read out of — or overlap — the amount's own characters. The
  // final `cleanDescription` strip below still operates on the true original
  // string; this working copy exists only to keep the two matchers isolated.
  final masked = amountToken == null
      ? input
      : input.replaceRange(
          amountToken.start,
          amountToken.end,
          ' ' * (amountToken.end - amountToken.start),
        );

  SmartToken? projectToken;
  String? project;
  final pm = projectTokenPattern.firstMatch(masked);
  if (pm != null) {
    final start = pm.start + (pm.group(1)?.length ?? 0);
    project = pm.group(2);
    projectToken = SmartToken(start, pm.end, SmartTokenKind.project);
  }

  final tokens = [
    ?amountToken,
    ?projectToken,
  ]..sort((a, b) => a.start.compareTo(b.start));

  // Strip the accepted spans from the ORIGINAL input, right-to-left (so an
  // earlier removal doesn't shift a later span's indices), then collapse
  // whitespace — mirrors parseSmartAdd's clean-title construction.
  var stripped = input;
  for (final t in [...tokens]..sort((a, b) => b.start.compareTo(a.start))) {
    stripped = stripped.replaceRange(t.start, t.end, ' ');
  }
  final cleanDescription = stripped.replaceAll(_whitespace, ' ').trim();

  return ParsedExpense(
    cleanDescription: cleanDescription,
    amount: amount,
    currency: currency,
    project: project,
    tokens: tokens,
  );
}

/// Map a matched leading [symbol] (`€`/`$`/`£`) and/or trailing [code]
/// (`EUR`/`USD`/`GBP`/`JPY`, or a symbol repeated in trailing position) to an
/// ISO currency code. The trailing group wins when both rode along (a rare,
/// redundant input like `€25 USD`); returns null when neither matched.
String? _resolveCurrency(String? symbol, String? code) {
  final raw = code ?? symbol;
  if (raw == null) return null;
  switch (raw.toUpperCase()) {
    case '€':
    case 'EUR':
      return 'EUR';
    case '\$':
    case 'USD':
      return 'USD';
    case '£':
    case 'GBP':
      return 'GBP';
    case 'JPY':
      return 'JPY';
  }
  return null; // unreachable given _amountPattern's own alternation; safe.
}
