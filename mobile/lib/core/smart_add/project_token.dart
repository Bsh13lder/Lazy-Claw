/// Canonical `#project` / `/project` token recognition.
///
/// The single source of truth both the task parser (`smart_add_parser.dart`)
/// and the expense parser (`smart_add_expense_parser.dart`) match against, so
/// `#project` behavior can never drift between the two sibling parsers.
///
/// A plain (non-`part of`) library, deliberately: both sibling parsers
/// `import` it directly instead of each declaring their own copy of the
/// pattern.
library;

/// `#name` or `/name` at a token boundary. The token-boundary anchor protects
/// against `and/or` (slash mid-word) and `6/10` (M/D dates, or an amount
/// masquerading as one) being misread as a project reference.
final RegExp projectTokenPattern = RegExp(r'(^|\s)[#/]([A-Za-z0-9_-]+)');

/// Remove the first `#token`/`/token` project reference from [title],
/// collapsing the leftover double space and trimming the ends. A no-op
/// (returns [title] unchanged) when no token is present.
String removeProjectToken(String title) {
  final m = projectTokenPattern.firstMatch(title);
  if (m == null) return title;
  final start = m.start + (m.group(1)?.length ?? 0);
  final stripped = title.replaceRange(start, m.end, '');
  return stripped.replaceAll(RegExp(r'\s+'), ' ').trim();
}
