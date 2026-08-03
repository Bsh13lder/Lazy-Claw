// Unit tests for the ADD-TASK amount detector — a COMPOSITION of the existing
// `parseSmartAdd` (task grammar) and `parseSmartExpense` (money grammar), not
// a new matcher inside either of them.
//
// The load-bearing property under test is the DIGIT-STEALING guard: the task
// field already parses dates, times, priorities and recurrences, all of which
// are digit-bearing. Adding a number-hungry amount matcher to that field is
// exactly the shape that produced this project's 13 prior smart-add false
// positives and the `mar/march/may` digit-collision bug (772d34d, 6c3ad85).
// So every collision case the task-parser suite already exercises is re-pinned
// here from the amount side: if `parseSmartAdd` consumed the digits, the
// amount detector MUST see nothing.
//
// `now` is pinned to Saturday 2026-06-06, matching smart_add_parser_test.dart,
// so relative-date math is deterministic. Pure Dart — no I/O, no async.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/smart_add_parser.dart';
import 'package:lazyclaw_mobile/core/smart_add_task_expense.dart';

void main() {
  // Saturday, 6 June 2026.
  final now = DateTime(2026, 6, 6);

  ParsedTask parse(String s) => parseSmartAdd(s, now: now);
  TaskExpenseMatch? detect(String s) => detectTaskExpense(parse(s), s);

  /// The raw text the detector claimed as the amount token.
  String tokenText(String s, TaskExpenseMatch m) =>
      s.substring(m.token.start, m.token.end);

  group('recognizes money', () {
    test('trailing ISO code arms by default', () {
      const input = 'buy paint 40 eur #home tomorrow';
      final m = detect(input);
      expect(m, isNotNull);
      expect(m!.amount, 40);
      expect(m.currency, 'EUR');
      expect(m.hasCurrencyMarker, isTrue);
      expect(tokenText(input, m), '40 eur');
      expect(m.token.kind, SmartTokenKind.amount);
    });

    test('leading symbol arms by default', () {
      const input = 'coffee \$12.50 today';
      final m = detect(input);
      expect(m, isNotNull);
      expect(m!.amount, 12.5);
      expect(m.currency, 'USD');
      expect(m.hasCurrencyMarker, isTrue);
      expect(tokenText(input, m), '\$12.50');
    });

    test('a bare number is detected but carries NO currency marker', () {
      const input = 'buy 2 apples';
      final m = detect(input);
      expect(m, isNotNull);
      expect(m!.amount, 2);
      expect(m.currency, isNull);
      expect(m.hasCurrencyMarker, isFalse);
    });

    test('an explicit currency later in the line beats an earlier bare number',
        () {
      // Otherwise "buy 3 tickets" would file a $3 expense and silently ignore
      // the $60 the user actually typed — the worst possible outcome for a
      // surface that spends money.
      const input = 'buy 3 tickets \$60 tomorrow';
      final m = detect(input);
      expect(m, isNotNull);
      expect(m!.amount, 60);
      expect(m.currency, 'USD');
      expect(tokenText(input, m), '\$60');
    });

    test('no digits at all → no detection', () {
      expect(detect('call mum tomorrow'), isNull);
    });
  });

  group('does NOT steal digits from a task token', () {
    // Every case here is a digit-bearing pattern the task parser already owns
    // (and already has a pinned test for in smart_add_parser_test.dart /
    // smart_add_recurrence_test.dart). The amount detector must see nothing.
    // NOTE: a bare "at 9" (no am/pm, no `:mm`) is deliberately absent — the
    // task parser does not recognize it as a time, so that digit is genuinely
    // free. It is covered in the next group instead.
    const taskOwned = <String>[
      'meeting 17:00', // 24h clock
      'meeting at 17:00', // cued 24h clock
      'call 9:30am', // 12h clock with minutes
    ];

    for (final input in taskOwned) {
      test('"$input" → no amount', () {
        expect(detect(input), isNull, reason: input);
      });
    }

    test('"in 2h" is a time, not two euros', () {
      expect(detect('ping bob in 2h'), isNull);
    });

    test('"in 3 days" is a date', () {
      expect(detect('ship it in 3 days'), isNull);
    });

    test('"march 3rd" is a date (ordinal disambiguated)', () {
      expect(detect('gala march 3rd'), isNull);
    });

    test('an ISO date is not an amount', () {
      expect(detect('file taxes 2026-06-30'), isNull);
    });

    test('"6/10" M/D date is not an amount', () {
      expect(detect('dinner 6/10'), isNull);
    });

    test('"+3d" offset is not an amount', () {
      expect(detect('follow up +3d'), isNull);
    });

    test('"!p1" priority is not an amount', () {
      expect(detect('urgent thing !p1'), isNull);
    });

    test('"1:1 every monday 5pm" leaves no free digits', () {
      // The `1:1` is not a task token either, but the amount pattern's own
      // token-boundary lookahead rejects it — pinned so a future loosening of
      // that pattern can't silently turn a meeting name into money.
      expect(detect('1:1 every monday 5pm'), isNull);
    });
  });

  group('digits the task parser deliberately leaves alone', () {
    // These are the cases where a digit legitimately survives the task parse.
    // The detector MAY see them — but with no currency marker, so the caller
    // never arms the expense by default.
    test('"meeting at 3" detects 3 with no currency marker', () {
      final m = detect('meeting at 3');
      expect(m, isNotNull);
      expect(m!.amount, 3);
      expect(m.hasCurrencyMarker, isFalse);
    });

    test('"every 2 weeks" detects 2 with no currency marker', () {
      // VERIFIED against the real parser, not assumed: `parseSmartAdd` has no
      // INTERVAL recurrence matcher — "every 2 weeks" produces no recurrence
      // and no token at all (only pinned forms like "every monday" / "every
      // day" match). So the 2 is genuinely free text, and the only thing
      // standing between it and a bogus money row is the currency-marker gate.
      final parsed = parse('water plants every 2 weeks');
      expect(parsed.recurrence, isNull, reason: 'guards the premise above');
      expect(parsed.tokens, isEmpty);

      final m = detect('water plants every 2 weeks');
      expect(m, isNotNull);
      expect(m!.amount, 2);
      expect(m.hasCurrencyMarker, isFalse);
    });

    test('"we march 3 miles today" detects 3 with no currency marker', () {
      // The task parser REFUSES "march 3" here (mar/march/may digit-collision
      // guard), so the 3 is free. It must not arm anything.
      final parsed = parse('we march 3 miles today');
      expect(parsed.cleanTitle, 'we march 3 miles');
      final m = detectTaskExpense(parsed, 'we march 3 miles today');
      expect(m, isNotNull);
      expect(m!.hasCurrencyMarker, isFalse);
    });
  });

  group('parsing is never corrupted by the detector', () {
    // The detector reads the ParsedTask; it must not be able to change it.
    // These assertions duplicate task-parser expectations on purpose: if a
    // future refactor moves amount detection INTO parseSmartAdd, these fail.
    test('a currency-bearing line still parses its date/project', () {
      const input = 'buy paint 40 eur #home tomorrow';
      final r = parse(input);
      expect(r.dueDate, '2026-06-07');
      expect(r.project, 'home');
      // The task parser alone leaves the money in the title...
      expect(r.cleanTitle, 'buy paint 40 eur');
      // ...and only the caller-side strip (below) removes it.
      final m = detectTaskExpense(r, input);
      expect(titleWithoutExpenseToken(input, r, m!.token), 'buy paint');
    });

    test('recurrence + time survive alongside a detected amount', () {
      const input = 'gym 20 usd every monday 5pm';
      final r = parse(input);
      expect(r.dueDate, '2026-06-08T17:00:00');
      expect(r.recurrence, isNotNull);
      final m = detectTaskExpense(r, input);
      expect(m!.amount, 20);
      expect(titleWithoutExpenseToken(input, r, m.token), 'gym');
    });
  });

  group('titleWithoutExpenseToken', () {
    test('removes the amount span and collapses whitespace', () {
      const input = 'buy 40 eur paint';
      final r = parse(input);
      final m = detectTaskExpense(r, input)!;
      expect(titleWithoutExpenseToken(input, r, m.token), 'buy paint');
    });

    test('an amount-only line strips to empty (caller falls back)', () {
      const input = '40 eur';
      final r = parse(input);
      final m = detectTaskExpense(r, input)!;
      expect(titleWithoutExpenseToken(input, r, m.token), '');
    });
  });

  group('merged highlight spans', () {
    test('the amount token merges into the task spans, sorted, non-overlapping',
        () {
      const input = 'buy paint 40 eur #home tomorrow';
      final r = parse(input);
      final m = detectTaskExpense(r, input)!;
      final merged = mergeExpenseToken(r.tokens, m.token);

      expect(merged.length, r.tokens.length + 1);
      var cursor = 0;
      for (final t in merged) {
        expect(t.start, greaterThanOrEqualTo(cursor));
        expect(t.end, greaterThan(t.start));
        expect(t.end, lessThanOrEqualTo(input.length));
        cursor = t.end;
      }
      expect(
        merged.where((t) => t.kind == SmartTokenKind.amount).length,
        1,
      );
    });
  });
}
