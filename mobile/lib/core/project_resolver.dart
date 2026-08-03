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
///   - `fuzzy`:      [_sequenceMatcherRatio] >= [kProjectFuzzyMinRatio] — a
///     faithful port of Python's `difflib.SequenceMatcher.ratio()`, the
///     exact function the agent-side resolver uses (see its doc for why a
///     cheaper approximation isn't good enough here)
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

/// Minimum [_sequenceMatcherRatio] for the fuzzy tier — ported verbatim from
/// the Python resolver's `FUZZY_MIN_RATIO = 0.85`.
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
      .where(
        (p) => _sequenceMatcherRatio(q, _norm(p.name)) >= kProjectFuzzyMinRatio,
      )
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

/// A faithful port of Python's `difflib.SequenceMatcher(None, a, b).ratio()`
/// (Ratcliff/Obershelp) — NOT an approximation. A prior review round used a
/// normalized-Levenshtein stand-in here, which coincides with
/// `SequenceMatcher` for pure single-character substitutions but is
/// consistently LESS generous for insertions/deletions/transpositions — e.g.
/// query `clubay` against candidates `clubhay` and `clubbzay` scores 0.923
/// and 0.857 under `SequenceMatcher` (both clear 0.85 → 2 hits → ambiguous →
/// ask back) but only 0.857 and 0.75 under Levenshtein (1 hit → silently
/// resolves to `clubhay`) — a wrong-money write to the wrong project that
/// the agent side's resolver would have refused. A real port removes the
/// discrepancy by construction instead of trying to characterize it.
///
/// No "autojunk" heuristic: Python's `SequenceMatcher` only applies autojunk
/// to sequences longer than 200 characters (treating a character that makes
/// up >1% of a 200+-length `b` as noise), which project names never
/// approach — omitting it is exact for this input size, not an
/// approximation of it.
///
/// Known limitation (see the task report for the full discussion; immaterial
/// for the ASCII project names this app expects): [a] and [b] are indexed by
/// Dart `String`'s UTF-16 code unit, not grapheme cluster, so a name
/// containing an emoji or another character outside the Basic Multilingual
/// Plane scores its surrogate pair as two separate units rather than one
/// grapheme.
double _sequenceMatcherRatio(String a, String b) {
  final total = a.length + b.length;
  if (total == 0) return 1.0;
  return 2.0 * _totalMatchingLength(a, b) / total;
}

/// Sum of all matching-block lengths from the same recursive
/// longest-matching-block search `SequenceMatcher.get_matching_blocks` uses:
/// find the single longest contiguous run common to `a[alo:ahi]` and
/// `b[blo:bhi]`, then recurse on the slices strictly left and strictly right
/// of that run.
int _totalMatchingLength(String a, String b) {
  final b2j = <String, List<int>>{};
  for (var j = 0; j < b.length; j++) {
    b2j.putIfAbsent(b[j], () => []).add(j);
  }

  var total = 0;
  final stack = <List<int>>[
    [0, a.length, 0, b.length],
  ];
  while (stack.isNotEmpty) {
    final range = stack.removeLast();
    final alo = range[0], ahi = range[1], blo = range[2], bhi = range[3];
    final match = _findLongestMatch(a, b2j, alo, ahi, blo, bhi);
    final i = match[0], j = match[1], k = match[2];
    if (k > 0) {
      total += k;
      if (alo < i && blo < j) stack.add([alo, i, blo, j]);
      if (i + k < ahi && j + k < bhi) stack.add([i + k, ahi, j + k, bhi]);
    }
  }
  return total;
}

/// Port of `SequenceMatcher.find_longest_match`: the longest run common to
/// `a[alo:ahi]` and `b[blo:bhi]`, returned as `[besti, bestj, bestsize]`
/// (start-in-`a`, start-in-`b`, length). [b2j] maps each character in `b` to
/// its (ascending) list of indices, built once per [_sequenceMatcherRatio]
/// call and reused across the whole recursion.
List<int> _findLongestMatch(
  String a,
  Map<String, List<int>> b2j,
  int alo,
  int ahi,
  int blo,
  int bhi,
) {
  var besti = alo, bestj = blo, bestsize = 0;
  var j2len = <int, int>{};
  for (var i = alo; i < ahi; i++) {
    final newj2len = <int, int>{};
    final indices = b2j[a[i]];
    if (indices != null) {
      for (final j in indices) {
        if (j < blo) continue;
        if (j >= bhi) break; // indices are ascending — safe to stop here
        final k = (j2len[j - 1] ?? 0) + 1;
        newj2len[j] = k;
        if (k > bestsize) {
          besti = i - k + 1;
          bestj = j - k + 1;
          bestsize = k;
        }
      }
    }
    j2len = newj2len;
  }
  return [besti, bestj, bestsize];
}
