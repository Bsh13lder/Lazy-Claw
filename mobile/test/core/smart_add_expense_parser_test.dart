// Unit tests for the on-device smart-add EXPENSE parser — a SIBLING to
// `parseSmartAdd` (smart_add_parser_test.dart), not an extension of it.
//
// Expense lines are digit-dominated, so this parser deliberately loads ONLY
// amount + currency + `#`/`/` project — no date/time/priority/recurrence
// matcher exists here in v1. The anti-pattern group below pins that these
// task-parser concepts genuinely cannot misfire, because the matchers simply
// aren't loaded (see Task 10 of
// docs/superpowers/plans/2026-08-03-sync-widget-parser-expenses.md).
//
// Pure Dart — no I/O, no LLM, no async.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/smart_add_expense_parser.dart';
import 'package:lazyclaw_mobile/core/smart_add_parser.dart' show SmartTokenKind;

/// Asserts [parsed]'s token spans (as parsed from [input]) are well-formed:
/// each is non-empty and in-bounds, and the whole list is ascending by
/// `start` with no two spans overlapping. Local to this file (rather than
/// reusing smart_add_test_helpers.dart's [ParsedTask]-typed helper) since
/// [ParsedExpense] is a different type.
void expectWellFormedSpans(String input, ParsedExpense parsed) {
  var cursor = 0;
  for (final t in parsed.tokens) {
    expect(
      t.start,
      inInclusiveRange(0, input.length),
      reason: 'token start out of bounds for "$input": $t',
    );
    expect(
      t.end,
      inInclusiveRange(t.start + 1, input.length),
      reason: 'token end out of bounds (or empty span) for "$input": $t',
    );
    expect(
      t.start,
      greaterThanOrEqualTo(cursor),
      reason:
          'tokens must be ascending and non-overlapping for "$input": '
          '$t starts before the previous token ended (cursor=$cursor)',
    );
    cursor = t.end;
  }
}

void main() {
  ParsedExpense parse(String s) {
    final r = parseSmartExpense(s);
    expectWellFormedSpans(s, r);
    return r;
  }

  group('the user\'s literal example + core fixtures', () {
    test('spent on #clubbay 25', () {
      final r = parse('spent on #clubbay 25');
      expect(r.amount, 25.0);
      expect(r.project, 'clubbay');
      expect(r.currency, isNull);
      expect(r.cleanDescription, 'spent on');
    });

    test('25 #clubbay lunch with team — amount first, project after', () {
      final r = parse('25 #clubbay lunch with team');
      expect(r.amount, 25.0);
      expect(r.project, 'clubbay');
      expect(r.currency, isNull);
      expect(r.cleanDescription, 'lunch with team');
    });

    test('€45.50 hosting /nima — leading symbol + slash project', () {
      final r = parse('€45.50 hosting /nima');
      expect(r.amount, 45.50);
      expect(r.currency, 'EUR');
      expect(r.project, 'nima');
      expect(r.cleanDescription, 'hosting');
    });

    test('12.90 coffee — bare decimal amount, no project', () {
      final r = parse('12.90 coffee');
      expect(r.amount, 12.90);
      expect(r.project, isNull);
      expect(r.currency, isNull);
      expect(r.cleanDescription, 'coffee');
    });

    test('40 eur #nima — trailing lower-case currency code', () {
      final r = parse('40 eur #nima');
      expect(r.amount, 40.0);
      expect(r.currency, 'EUR');
      expect(r.project, 'nima');
      expect(r.cleanDescription, '');
    });
  });

  group('currency symbol/code resolution', () {
    test('trailing GBP code', () {
      final r = parse('20 GBP taxi');
      expect(r.amount, 20.0);
      expect(r.currency, 'GBP');
    });

    test('leading dollar symbol', () {
      final r = parse(r'$9.99 snacks');
      expect(r.amount, 9.99);
      expect(r.currency, 'USD');
    });

    test('trailing JPY code', () {
      final r = parse('500 jpy ramen');
      expect(r.amount, 500.0);
      expect(r.currency, 'JPY');
    });

    test('bare amount with no symbol/code carries no currency — matches the '
        'existing form-based add\'s own fallback path (see the widget test '
        'for the full pin), so quick-typing introduces no NEW divergence',
        () {
      final r = parse('25 #clubbay lunch');
      expect(r.currency, isNull);
    });
  });

  group('no project when none typed', () {
    test('bare amount, no description leftover', () {
      final r = parse('30');
      expect(r.amount, 30.0);
      expect(r.project, isNull);
      expect(r.cleanDescription, '');
    });

    test('no amount, no project — text passes through untouched', () {
      final r = parse('just a note');
      expect(r.amount, isNull);
      expect(r.project, isNull);
      expect(r.cleanDescription, 'just a note');
    });

    test(
        'coffee #cafe — the project matcher runs UNCONDITIONALLY, with or '
        'without an amount (pins ParsedExpense.amount\'s doc: "mandatory '
        'anchor" describes masking ORDER, not a gate on project detection)',
        () {
      final r = parse('coffee #cafe');
      expect(r.amount, isNull);
      expect(r.project, 'cafe');
      expect(r.cleanDescription, 'coffee');
    });
  });

  group('anti-patterns that must NOT misfire (no date/time/priority/'
      'recurrence matcher is loaded here)', () {
    test('parking !2 — no priority concept exists in this parser', () {
      final r = parse('parking !2');
      expect(r.amount, isNull);
      expect(r.project, isNull);
      expect(r.cleanDescription, 'parking !2');
    });

    test('25,50 groceries — comma is never a decimal point', () {
      final r = parse('25,50 groceries');
      // The trailing-boundary lookahead fails right after the first digit
      // run (the comma is neither `.` nor a token boundary), so the WHOLE
      // match fails — this pins the parser never reads it as 25.5 either.
      expect(r.amount, isNull);
      expect(r.amount, isNot(25.5));
    });

    test('6/10 dinner — no date matcher in v1; "6/10" isn\'t an amount or a '
        'project either', () {
      final r = parse('6/10 dinner');
      expect(r.amount, isNull);
      expect(r.project, isNull);
      expect(r.cleanDescription, '6/10 dinner');
    });

    test('a URL must not become a project', () {
      final r = parse('lunch http://example.com/team 20');
      expect(r.project, isNull);
      expect(r.amount, 20.0); // the trailing amount still parses fine
    });
  });

  group('span bookkeeping', () {
    test('tokens are sorted by start and reusable for highlighting', () {
      final r = parse('25 #clubbay lunch with team');
      expect(r.tokens.length, 2);
      expect(r.tokens[0].start < r.tokens[1].start, isTrue);
    });

    test('masking keeps the amount span and the project span disjoint', () {
      final r = parse('spent on #clubbay 25');
      final amountTok = r.tokens.firstWhere(
        (t) => t.kind == SmartTokenKind.amount,
      );
      final projectTok = r.tokens.firstWhere(
        (t) => t.kind == SmartTokenKind.project,
      );
      expect(amountTok.start >= projectTok.end, isTrue);
    });
  });
}
