// Unit tests for resolveProjectMatch — mirrors the agent-side resolver
// (lazyclaw/budgets/resolver.py:resolve_project) tier-for-tier: exact,
// then substring, then fuzzy, each single-hit-only; more than one hit at a
// tier stops resolution right there (ambiguous, never guess).

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/project_resolver.dart';
import 'package:lazyclaw_mobile/models/project.dart';

Project _project(String id, String name) => Project(
      id: id,
      name: name,
      budget: 0,
      currency: 'USD',
      status: 'active',
    );

void main() {
  group('exact tier', () {
    test('single exact match (case/whitespace-insensitive) resolves', () {
      final projects = [_project('p1', 'Groceries')];
      expect(resolveProjectMatch('groceries', projects)?.id, 'p1');
      expect(resolveProjectMatch('  Groceries  ', projects)?.id, 'p1');
    });
  });

  group('substring tier (the review-flagged gap)', () {
    test('"Clubbay VIP" + #clubbay auto-applies via a single substring hit',
        () {
      final projects = [_project('p1', 'Clubbay VIP')];
      expect(resolveProjectMatch('clubbay', projects)?.id, 'p1');
    });

    test('two projects both containing "club" — ambiguous, no silent guess',
        () {
      final projects = [
        _project('p1', 'Clubhouse'),
        _project('p2', 'Nightclub'),
      ];
      expect(resolveProjectMatch('club', projects), isNull);
    });
  });

  group('fuzzy tier', () {
    test('a single close-typo hit auto-applies', () {
      // "clubbay" vs "clubbey": one substitution in 7 chars -> ratio ~0.857,
      // clears the 0.85 threshold.
      final projects = [_project('p1', 'Clubbey')];
      expect(resolveProjectMatch('clubbay', projects)?.id, 'p1');
    });

    test('two equally-close fuzzy hits — ambiguous, no silent guess', () {
      // "clubbaz" is a single-substitution (last char) away from BOTH names
      // below (distance 1 / length 7 = ratio ~0.857, clears 0.85 for each),
      // and an exact/substring match for neither — so both tiers above fall
      // through to fuzzy, which then finds 2 hits and refuses to guess.
      final projects = [_project('p1', 'Clubbax'), _project('p2', 'Clubbay')];
      expect(resolveProjectMatch('clubbaz', projects), isNull);
    });

    test('a distant name does not fuzzy-match', () {
      final projects = [_project('p1', 'Marketing')];
      expect(resolveProjectMatch('clubbay', projects), isNull);
    });

    test(
        'regression (review round 3): an insertion-class near-duplicate pair '
        'must NOT silently resolve — this is the exact case where a cheaper '
        'Levenshtein stand-in diverged from Python\'s SequenceMatcher and '
        'would have written the expense to the wrong project', () {
      // query "clubay" vs "clubhay" (a single INSERTION, not a substitution)
      // and "clubbzay" (a single insertion + is also one substitution away
      // from "clubbay"-shaped names). Under difflib.SequenceMatcher these
      // score 0.923 and 0.857 — BOTH clear 0.85, so the real agent-side
      // resolver treats this as ambiguous ("multi") and asks back. A
      // normalized-Levenshtein approximation instead scored 0.857 and 0.75
      // — only ONE hit — and would have silently picked "clubhay". Neither
      // name is an exact or substring match for "clubay", so both fall
      // through to the fuzzy tier, which must find BOTH hits and refuse to
      // guess.
      final projects = [
        _project('p1', 'clubhay'),
        _project('p2', 'clubbzay'),
      ];
      expect(resolveProjectMatch('clubay', projects), isNull);
    });
  });

  group('no match / empty query', () {
    test('no project comes close — null (falls back to disambiguation UI)',
        () {
      final projects = [_project('p1', 'Marketing'), _project('p2', 'Home')];
      expect(resolveProjectMatch('nima', projects), isNull);
    });

    test('empty query resolves to null', () {
      final projects = [_project('p1', 'Groceries')];
      expect(resolveProjectMatch('', projects), isNull);
      expect(resolveProjectMatch('   ', projects), isNull);
    });

    test('empty project list resolves to null', () {
      expect(resolveProjectMatch('clubbay', const []), isNull);
    });
  });

  group('tier precedence: exact wins even when it would also substring-match',
      () {
    test('exact match short-circuits before the substring tier runs', () {
      final projects = [
        _project('p1', 'club'),
        _project('p2', 'clubhouse'),
      ];
      // "club" is an EXACT match for p1. It's also a substring of p2's name,
      // but the exact tier resolves first and stops there — p1 wins, not an
      // ambiguous "multi".
      expect(resolveProjectMatch('club', projects)?.id, 'p1');
    });
  });
}
