// Unit tests for the recurrence vocabulary in the on-device smart-add parser.
//
// `now` is pinned to Saturday 2026-06-06 so weekday math is deterministic.
// The parser is pure Dart — no I/O, no LLM, no async.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/recurrence.dart';
import 'package:lazyclaw_mobile/core/smart_add_parser.dart';

import 'smart_add_test_helpers.dart';

void main() {
  // Saturday, 6 June 2026.
  final now = DateTime(2026, 6, 6);
  ParsedTask parse(String s) {
    final r = parseSmartAdd(s, now: now);
    expectWellFormedSpans(s, r);
    return r;
  }

  group('recurrence vocabulary', () {
    test('"every day" → daily, stripped from the title', () {
      final r = parse('water plants every day');
      expect(r.recurrence, Recurrence.daily);
      expect(r.cleanTitle, 'water plants');
    });

    test('"daily" → daily', () {
      expect(parse('standup daily').recurrence, Recurrence.daily);
    });

    test('"everyday" → daily', () {
      expect(parse('journal everyday').recurrence, Recurrence.daily);
    });

    test('"every weekday" → weekdays', () {
      final r = parse('check email every weekday');
      expect(r.recurrence, Recurrence.weekdays);
      expect(r.cleanTitle, 'check email');
    });

    test('"weekdays" → weekdays', () {
      expect(parse('commute weekdays').recurrence, Recurrence.weekdays);
    });

    test('"every month" → monthly', () {
      expect(parse('pay rent every month').recurrence, Recurrence.monthly);
    });

    test('"monthly" → monthly', () {
      expect(parse('report monthly').recurrence, Recurrence.monthly);
    });

    test('"every year" → yearly', () {
      expect(parse('renew domain every year').recurrence, Recurrence.yearly);
    });

    test('"yearly" → yearly', () {
      expect(parse('taxes yearly').recurrence, Recurrence.yearly);
    });

    test('"annually" → yearly', () {
      expect(parse('audit annually').recurrence, Recurrence.yearly);
    });

    test('no recurrence vocab → null recurrence', () {
      expect(parse('buy milk tomorrow').recurrence, isNull);
    });
  });

  group('weekly recurrence', () {
    test('"every week" → weekly, weekday anchored to today (Sat=6)', () {
      final r = parse('review every week');
      expect(r.recurrence?.kind, RecurrenceKind.weekly);
      // No explicit date → anchored to today (Saturday).
      expect(r.recurrence?.weekday, DateTime.saturday);
      expect(r.cleanTitle, 'review');
    });

    test('"weekly" with an explicit due date anchors weekday to THAT date', () {
      // tomorrow = Sunday 2026-06-07.
      final r = parse('review tomorrow weekly');
      expect(r.recurrence?.kind, RecurrenceKind.weekly);
      expect(r.recurrence?.weekday, DateTime.sunday);
      expect(r.dueDate, '2026-06-07');
    });
  });

  group('"every <weekday>"', () {
    test(
      '"every monday" → weekly on Monday + implied next-Monday due date',
      () {
        final r = parse('team sync every monday');
        expect(r.recurrence?.kind, RecurrenceKind.weekly);
        expect(r.recurrence?.weekday, DateTime.monday);
        // From Saturday 6 June, the next Monday is 8 June.
        expect(r.dueDate, '2026-06-08');
        expect(r.cleanTitle, 'team sync');
      },
    );

    test('"every fri" short form → weekly on Friday', () {
      final r = parse('deploy every fri');
      expect(r.recurrence?.kind, RecurrenceKind.weekly);
      expect(r.recurrence?.weekday, DateTime.friday);
      // Next Friday from Saturday 6 June is 12 June.
      expect(r.dueDate, '2026-06-12');
    });

    test('explicit date wins over the "every monday" implied date', () {
      // tomorrow = Sunday 7 June; recurrence weekday stays Monday.
      final r = parse('plan tomorrow every monday');
      expect(r.dueDate, '2026-06-07');
      expect(r.recurrence?.weekday, DateTime.monday);
    });

    test('"every sunday" maps to Dart weekday 7', () {
      final r = parse('rest every sunday');
      expect(r.recurrence?.weekday, DateTime.sunday);
      // Next Sunday from Saturday 6 June is 7 June.
      expect(r.dueDate, '2026-06-07');
    });
  });

  group('recurrence → cron composition (as the add sheet does it)', () {
    test('"every monday 5pm" composes a weekly-Monday cron at 17:00', () {
      final r = parse('1:1 every monday 5pm');
      expect(r.dueDate, '2026-06-08T17:00:00');
      final anchor = DateTime.tryParse(r.dueDate!);
      final cron = recurrenceToCron(r.recurrence!, dueAnchor: anchor);
      expect(cron, '0 17 * * 1');
    });

    test('"pay rent every month" with no time → monthly at default 09:00', () {
      final r = parse('pay rent every month');
      expect(r.dueDate, isNull); // no due date / time given
      final cron = recurrenceToCron(r.recurrence!);
      expect(cron, '0 9 1 * *'); // no anchor → day 1, 09:00
    });
  });

  group('token spans + interaction with other tokens', () {
    test('recurrence coexists with priority and project', () {
      final r = parse('standup every day !p1 #work');
      expect(r.recurrence, Recurrence.daily);
      expect(r.priority, 'urgent');
      expect(r.project, 'work');
      expect(r.cleanTitle, 'standup');
    });

    test(
      '"every monday" yields a single recurrence span (not a date span)',
      () {
        const input = 'sync every monday';
        final r = parse(input);
        final recur = r.tokens
            .where((t) => t.kind == SmartTokenKind.recurrence)
            .toList();
        expect(recur.length, 1);
        expect(
          input.substring(recur.first.start, recur.first.end),
          'every monday',
        );
        // No standalone date span swallowed "monday".
        expect(r.tokens.where((t) => t.kind == SmartTokenKind.date), isEmpty);
      },
    );

    test('only the first recurrence wins on conflicting vocab', () {
      // "daily" then "weekly": earliest-start span (daily) is kept.
      final r = parse('thing daily weekly');
      expect(r.recurrence, Recurrence.daily);
    });
  });

  group('signature stability', () {
    test('existing fields still parse with recurrence added', () {
      final r = parse('buy milk tomorrow !p1 #groceries');
      expect(r.cleanTitle, 'buy milk');
      expect(r.dueDate, '2026-06-07');
      expect(r.priority, 'urgent');
      expect(r.project, 'groceries');
      expect(r.recurrence, isNull);
    });
  });
}
