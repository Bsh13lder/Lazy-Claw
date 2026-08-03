// Unit tests for the on-device "Todoist-style" smart-add parser.
//
// `now` is pinned to Saturday 2026-06-06 so weekday / relative-date math is
// deterministic. The parser is pure Dart — no I/O, no LLM, no async.

import 'package:flutter_test/flutter_test.dart';
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

  test('full token soup: date + priority + project', () {
    final r = parse('buy milk tomorrow !p1 #groceries');
    expect(r.cleanTitle, 'buy milk');
    expect(r.dueDate, '2026-06-07');
    expect(r.priority, 'urgent');
    expect(r.project, 'groceries');
  });

  test('weekday resolves to the next occurrence', () {
    final r = parse('call mon');
    expect(r.cleanTitle, 'call');
    expect(r.dueDate, '2026-06-08'); // next Monday after Sat 06-06
    expect(r.priority, isNull);
    expect(r.project, isNull);
  });

  test('weekday matching today resolves to today (future-or-today)', () {
    final r = parse('water plants sat');
    expect(r.cleanTitle, 'water plants');
    expect(r.dueDate, '2026-06-06');
  });

  test('bang shorthand !! maps to high, no date', () {
    final r = parse('pay rent !!');
    expect(r.cleanTitle, 'pay rent');
    expect(r.priority, 'high');
    expect(r.dueDate, isNull);
    expect(r.project, isNull);
  });

  test('triple bang maps to urgent', () {
    final r = parse('ship release !!!');
    expect(r.cleanTitle, 'ship release');
    expect(r.priority, 'urgent');
  });

  test('numeric priority codes map across the scale', () {
    expect(parse('a !1').priority, 'urgent');
    expect(parse('a !p2').priority, 'high');
    expect(parse('a !3').priority, 'medium');
    expect(parse('a !p4').priority, 'low');
  });

  test('slash project form', () {
    final r = parse('fix bug /backend !2');
    expect(r.cleanTitle, 'fix bug');
    expect(r.project, 'backend');
    expect(r.priority, 'high');
  });

  test('hash project keeps only the first project token', () {
    final r = parse('triage #ops #later');
    expect(r.project, 'ops');
    // Only the first project token is stripped per spec.
    expect(r.cleanTitle, 'triage #later');
  });

  test('bare title leaves everything null and untouched', () {
    final r = parse('buy groceries and call mum');
    expect(r.cleanTitle, 'buy groceries and call mum');
    expect(r.dueDate, isNull);
    expect(r.priority, isNull);
    expect(r.project, isNull);
  });

  test('M/D slash date resolves to this year', () {
    final r = parse('report 6/10');
    expect(r.cleanTitle, 'report');
    expect(r.dueDate, '2026-06-10');
  });

  test('explicit ISO date is taken verbatim', () {
    final r = parse('submit taxes 2026-04-15 !p3');
    expect(r.cleanTitle, 'submit taxes');
    expect(r.dueDate, '2026-04-15');
    expect(r.priority, 'medium');
  });

  test('today / tonight resolve to today', () {
    expect(parse('standup today').dueDate, '2026-06-06');
    expect(parse('cook tonight').dueDate, '2026-06-06');
  });

  test('next week is +7 days', () {
    final r = parse('ping team next week');
    expect(r.cleanTitle, 'ping team');
    expect(r.dueDate, '2026-06-13');
  });

  test('in N days offsets from now', () {
    final r = parse('review pr in 3 days');
    expect(r.cleanTitle, 'review pr');
    expect(r.dueDate, '2026-06-09');
  });

  test('bare 24h time implies today and keeps the time', () {
    final r = parse('meeting 17:00');
    expect(r.cleanTitle, 'meeting');
    expect(r.dueDate, '2026-06-06T17:00:00');
    expect(r.hasTime, isTrue);
  });

  test('am/pm time implies today and keeps the time', () {
    final r = parse('gym 6pm');
    expect(r.cleanTitle, 'gym');
    expect(r.dueDate, '2026-06-06T18:00:00');
    expect(r.hasTime, isTrue);
  });

  test('am/pm time with minutes resolves to today at that time', () {
    final r = parse('call 9:30am');
    expect(r.cleanTitle, 'call');
    expect(r.dueDate, '2026-06-06T09:30:00');
    expect(r.hasTime, isTrue);
  });

  test('a date + a time combine into a datetime', () {
    final r = parse('meet tomorrow 5pm');
    expect(r.cleanTitle, 'meet');
    expect(r.dueDate, '2026-06-07T17:00:00');
    expect(r.hasTime, isTrue);
  });

  test('weekday/date supplies the day, the time supplies the clock', () {
    final r = parse('dentist tomorrow 9am');
    expect(r.cleanTitle, 'dentist');
    expect(r.dueDate, '2026-06-07T09:00:00'); // tomorrow at 9am
    expect(r.hasTime, isTrue);
  });

  test('M/D date + am/pm time combine', () {
    final r = parse('report 6/10 3pm');
    expect(r.cleanTitle, 'report');
    expect(r.dueDate, '2026-06-10T15:00:00');
    expect(r.hasTime, isTrue);
  });

  test('12am / 12pm map to midnight / noon', () {
    expect(parse('x 12am').dueDate, '2026-06-06T00:00:00');
    expect(parse('x 12pm').dueDate, '2026-06-06T12:00:00');
  });

  test('a bare date has no time and stays date-only', () {
    final r = parse('pay rent tomorrow');
    expect(r.cleanTitle, 'pay rent');
    expect(r.dueDate, '2026-06-07');
    expect(r.hasTime, isFalse);
  });

  test('no date and no time leaves dueDate null and hasTime false', () {
    final r = parse('pay rent');
    expect(r.dueDate, isNull);
    expect(r.hasTime, isFalse);
  });

  test('mid-word punctuation is never treated as a token', () {
    final r = parse('email john@acme.com asap!');
    // "asap!" ends with '!' mid-word -> not a priority token.
    expect(r.cleanTitle, 'email john@acme.com asap!');
    expect(r.priority, isNull);
    expect(r.project, isNull);
  });

  test('extra whitespace collapses in the clean title', () {
    final r = parse('buy   milk    tomorrow');
    expect(r.cleanTitle, 'buy milk');
    expect(r.dueDate, '2026-06-07');
  });

  test('empty input is safe', () {
    final r = parse('');
    expect(r.cleanTitle, '');
    expect(r.dueDate, isNull);
    expect(r.priority, isNull);
    expect(r.project, isNull);
  });

  test('out-of-range M/D is left in the title, not parsed as a date', () {
    final r = parse('mix 13/45 ratio');
    expect(r.dueDate, isNull);
    expect(r.cleanTitle, 'mix 13/45 ratio');
  });

  // ── Expanded vocabulary (abbreviations) ────────────────────────────────────

  group('relative-day abbreviations', () {
    test('tom / tmr / tmrw all resolve to tomorrow', () {
      expect(parse('ship tom').dueDate, '2026-06-07');
      expect(parse('ship tmr').dueDate, '2026-06-07');
      expect(parse('ship tmrw').dueDate, '2026-06-07');
      expect(parse('ship tom').cleanTitle, 'ship');
    });

    test('tod / tdy resolve to today', () {
      expect(parse('standup tod').dueDate, '2026-06-06');
      expect(parse('standup tdy').dueDate, '2026-06-06');
    });

    test('tn is today, date-only', () {
      final r = parse('cook tn');
      expect(r.dueDate, '2026-06-06');
      expect(r.hasTime, isFalse);
      expect(r.cleanTitle, 'cook');
    });

    test('yesterday is a negative offset', () {
      expect(parse('log yesterday').dueDate, '2026-06-05');
    });

    test('eod is today, eow is the upcoming Sunday', () {
      expect(parse('wrap eod').dueDate, '2026-06-06');
      expect(parse('wrap eow').dueDate, '2026-06-07'); // Sun after Sat 06-06
    });

    test('abbreviations never match mid-word', () {
      // "tomato" must not match "tom"; "today" not "tod"; "eodish" not "eod".
      final r = parse('buy tomato for eodish stew tonightly');
      expect(r.dueDate, isNull);
      expect(r.cleanTitle, 'buy tomato for eodish stew tonightly');
    });
  });

  group('next <unit>', () {
    test('nxt week is +7', () {
      expect(parse('sync nxt week').dueDate, '2026-06-13');
    });

    test('next month is the first of next month', () {
      expect(parse('invoice next month').dueDate, '2026-07-01');
      expect(parse('invoice nxt month').dueDate, '2026-07-01');
    });

    test('next year is Jan 1 of next year', () {
      expect(parse('plan next year').dueDate, '2027-01-01');
    });

    test('next <weekday> is the following-week occurrence', () {
      // Upcoming Fri after Sat 06-06 is 06-12; "next fri" is +7 -> 06-19.
      expect(parse('demo next fri').dueDate, '2026-06-19');
      expect(parse('demo nxt fri').dueDate, '2026-06-19');
      // Upcoming Mon is 06-08; "next mon" -> 06-15.
      expect(parse('demo next mon').dueDate, '2026-06-15');
    });

    test('next <weekday> swallows the bare weekday (no double token)', () {
      final r = parse('demo next fri');
      expect(r.cleanTitle, 'demo');
      expect(r.tokens.length, 1);
      expect(r.tokens.first.kind, SmartTokenKind.date);
    });
  });

  group('weekend', () {
    test('this weekend is the upcoming Saturday', () {
      expect(parse('trip this weekend').dueDate, '2026-06-06'); // Sat today
    });

    test('next weekend is that Saturday + 7', () {
      expect(parse('trip next weekend').dueDate, '2026-06-13');
      expect(parse('trip nxt weekend').dueDate, '2026-06-13');
    });
  });

  group('day-offset forms', () {
    test('in N weeks offsets by N*7 days', () {
      expect(parse('review in 2 weeks').dueDate, '2026-06-20');
    });

    test('+Nd and +N days both add days', () {
      expect(parse('renew +3d').dueDate, '2026-06-09');
      expect(parse('renew +2 days').dueDate, '2026-06-08');
    });
  });

  group('single-letter am/pm clock', () {
    test('9a -> 09:00, 9p -> 21:00', () {
      expect(parse('gym 9a').dueDate, '2026-06-06T09:00:00');
      expect(parse('gym 9p').dueDate, '2026-06-06T21:00:00');
    });

    test('12p -> noon, 12a -> midnight', () {
      expect(parse('x 12p').dueDate, '2026-06-06T12:00:00');
      expect(parse('x 12a').dueDate, '2026-06-06T00:00:00');
    });

    test('single letter never matches mid-word', () {
      final r = parse('eat 9apples');
      expect(r.dueDate, isNull);
      expect(r.cleanTitle, 'eat 9apples');
    });
  });

  group('time-of-day keywords', () {
    test('each keyword maps to its wall-clock hour', () {
      expect(parse('a morning').dueDate, '2026-06-06T09:00:00');
      expect(parse('a afternoon').dueDate, '2026-06-06T13:00:00');
      expect(parse('a evening').dueDate, '2026-06-06T18:00:00');
      expect(parse('a night').dueDate, '2026-06-06T20:00:00');
      expect(parse('a noon').dueDate, '2026-06-06T12:00:00');
      expect(parse('a midnight').dueDate, '2026-06-06T00:00:00');
    });

    test('a day token + a keyword combine (tom morning = tomorrow 09:00)', () {
      final r = parse('standup tom morning');
      expect(r.cleanTitle, 'standup');
      expect(r.dueDate, '2026-06-07T09:00:00');
      expect(r.hasTime, isTrue);
    });

    test('"tonight" stays date-only (night keyword does not fire inside it)', () {
      final r = parse('cook tonight');
      expect(r.dueDate, '2026-06-06');
      expect(r.hasTime, isFalse);
    });
  });

  group('in N hours (duration -> time)', () {
    test('sets a time of now + N hours', () {
      const input = 'ship in 2h';
      final r = parseSmartAdd(input, now: DateTime(2026, 6, 6, 14, 30));
      expectWellFormedSpans(input, r);
      expect(r.cleanTitle, 'ship');
      expect(r.dueDate, '2026-06-06T16:30:00');
      expect(r.hasTime, isTrue);
    });

    test('rolls the date forward when it crosses midnight', () {
      const input = 'call in 3 hours';
      final r = parseSmartAdd(input, now: DateTime(2026, 6, 6, 23, 0));
      expectWellFormedSpans(input, r);
      expect(r.dueDate, '2026-06-07T02:00:00');
      expect(r.hasTime, isTrue);
    });
  });

  group('bare priority codes', () {
    test('p1..p4 map across the scale', () {
      expect(parse('a p1').priority, 'urgent');
      expect(parse('a p2').priority, 'high');
      expect(parse('a p3').priority, 'medium');
      expect(parse('a p4').priority, 'low');
    });

    test('bare priority is stripped from the title', () {
      final r = parse('upgrade plan p2');
      expect(r.cleanTitle, 'upgrade plan');
      expect(r.priority, 'high');
    });

    test('case-insensitive', () {
      expect(parse('a P3').priority, 'medium');
    });
  });

  // ── Span correctness ───────────────────────────────────────────────────────

  group('token spans', () {
    test('spans index the ORIGINAL string and carry the right kinds', () {
      const input = 'buy milk tomorrow !p1 #groceries';
      final r = parse(input);
      expect(r.tokens.length, 3);
      // Each span's substring is exactly the token the user typed.
      String sub(SmartToken t) => input.substring(t.start, t.end);
      expect(sub(r.tokens[0]), 'tomorrow');
      expect(r.tokens[0].kind, SmartTokenKind.date);
      expect(sub(r.tokens[1]), '!p1');
      expect(r.tokens[1].kind, SmartTokenKind.priority);
      expect(sub(r.tokens[2]), '#groceries');
      expect(r.tokens[2].kind, SmartTokenKind.project);
    });

    test('date + time produce two adjacent, ordered spans', () {
      const input = 'meet tomorrow 5pm';
      final r = parse(input);
      expect(r.tokens.map((t) => input.substring(t.start, t.end)).toList(),
          ['tomorrow', '5pm']);
      expect(r.tokens.map((t) => t.kind).toList(),
          [SmartTokenKind.date, SmartTokenKind.time]);
    });

    test('spans are sorted by start and never overlap', () {
      // `parse()` already runs `expectWellFormedSpans` (ascending,
      // non-overlapping, in-bounds); this test pins the additional,
      // fixture-specific project-token assertion below.
      final r = parse('plan next fri 9am p2 #work later #ignored');
      // Only the FIRST project token is recognized.
      final projects =
          r.tokens.where((t) => t.kind == SmartTokenKind.project).toList();
      expect(projects.length, 1);
      expect(r.project, 'work');
    });

    test('a bare title produces no spans', () {
      expect(parse('just some text').tokens, isEmpty);
    });
  });

  group('removeProjectToken', () {
    test('strips the /token and collapses the leftover double space', () {
      expect(removeProjectToken('buy paint /gro now'), 'buy paint now');
    });

    test('strips a #token at the start of the title', () {
      expect(removeProjectToken('#groceries buy milk'), 'buy milk');
    });

    test('strips a trailing token with no text after it', () {
      expect(removeProjectToken('buy paint /gro'), 'buy paint');
    });

    test('no-ops when there is no project token', () {
      expect(removeProjectToken('buy paint now'), 'buy paint now');
    });
  });
}
