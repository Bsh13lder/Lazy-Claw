/// Resolves a free-text project name/token against a project list.
///
/// Mirrors the agent-side resolver (`lazyclaw/budgets/resolver.py:
/// resolve_project`, wired into `lazyclaw/skills/builtin/budget_manager.py`)
/// so a `#project` token resolves the SAME way whether it's typed on mobile
/// or handled by the agent — the exact gap a prior review round flagged: an
/// exact-match-only mobile resolver made a project like "Clubbay VIP" show a
/// disambiguation strip for `#clubbay` even though the agent side would have
/// silently auto-resolved it.
///
/// Tiers (precision-first, single-hit-only at each tier):
///   - `exact`:      normalized name == normalized query
///   - `substring`:  normalized query is CONTAINED IN the normalized name
///   - `fuzzy`:      similarity ratio >= [kProjectFuzzyMinRatio]
///
/// A tier with MORE THAN ONE hit is ambiguous and STOPS resolution right
/// there — it does not fall through to a later, looser tier (mirrors the
/// Python resolver's `"multi"` reason short-circuiting: never silently guess
/// among several candidates). Zero hits at a tier falls through to the next;
/// zero hits at every tier (or an empty query) resolves to null. Callers
/// treat null as "ambiguous or no match" and fall back to a disambiguation
/// UI, exactly like the agent side's ask-back.
library;

import 'package:lazyclaw_mobile/models/project.dart';

/// Minimum [_similarity] ratio for the fuzzy tier — matches the Python
/// resolver's `FUZZY_MIN_RATIO = 0.85`.
const double kProjectFuzzyMinRatio = 0.85;

/// Resolve [query] (typically a `#`/`/` token's bare text) against
/// [projects]. See the library doc for the tier order and ambiguity rule.
Project? resolveProjectMatch(String query, List<Project> projects) {
  final q = _norm(query);
  if (q.isEmpty) return null;

  final exact = projects.where((p) => _norm(p.name) == q).toList();
  if (exact.length == 1) return exact.first;
  if (exact.length > 1) return null; // ambiguous — never guess

  final substring = projects.where((p) => _norm(p.name).contains(q)).toList();
  if (substring.length == 1) return substring.first;
  if (substring.length > 1) return null;

  final fuzzy = projects
      .where((p) => _similarity(q, _norm(p.name)) >= kProjectFuzzyMinRatio)
      .toList();
  if (fuzzy.length == 1) return fuzzy.first;
  return null; // multi or none
}

/// Casefold + collapse whitespace — mirrors the server's own normalization
/// (`lazyclaw/budgets/store.py:_name_key`, `resolver.py:_norm`) closely
/// enough for the ASCII project names this app expects (Dart's
/// `toLowerCase` vs Python's `casefold` differ only on a handful of
/// non-ASCII code points, e.g. German ẞ, not expected in project names).
String _norm(String s) {
  final trimmed = s.trim().toLowerCase();
  if (trimmed.isEmpty) return '';
  return trimmed.split(RegExp(r'\s+')).join(' ');
}

/// A normalized-edit-distance similarity ratio in `[0, 1]`.
///
/// This is a DELIBERATE stand-in for the Python resolver's
/// `difflib.SequenceMatcher.ratio()` (Ratcliff/Obershelp) — not a port of
/// that specific algorithm. What the single-hit-only gating in
/// [resolveProjectMatch] actually needs from this function is "how close are
/// these two short strings", not a particular formula, and normalized
/// Levenshtein distance is a standard, well-understood measure for exactly
/// that job (a single-character edit in a short project name reliably scores
/// above 0.85; two unrelated names reliably score well below it).
double _similarity(String a, String b) {
  if (a == b) return 1.0;
  final maxLen = a.length > b.length ? a.length : b.length;
  if (maxLen == 0) return 1.0;
  return 1 - (_levenshtein(a, b) / maxLen);
}

/// Classic single-row dynamic-programming Levenshtein (edit) distance.
int _levenshtein(String a, String b) {
  final la = a.length;
  final lb = b.length;
  var prev = List<int>.generate(lb + 1, (j) => j);
  for (var i = 1; i <= la; i++) {
    final cur = List<int>.filled(lb + 1, 0);
    cur[0] = i;
    for (var j = 1; j <= lb; j++) {
      final cost = a[i - 1] == b[j - 1] ? 0 : 1;
      final deletion = prev[j] + 1;
      final insertion = cur[j - 1] + 1;
      final substitution = prev[j - 1] + cost;
      cur[j] = deletion < insertion
          ? (deletion < substitution ? deletion : substitution)
          : (insertion < substitution ? insertion : substitution);
    }
    prev = cur;
  }
  return prev[lb];
}
