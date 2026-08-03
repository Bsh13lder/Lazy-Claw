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
    // `sat` is a restricted short form (G1 #3) — it needs a disambiguating
    // cue ("on") to count as a date at all; see the `weekday false positives`
    // and `weekday cue disambiguation` groups below for the full matrix.
    final r = parse('water plants on sat');
    expect(r.cleanTitle, 'water plants on');
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

  test('today resolves to today; tonight resolves to today at 20:00', () {
    // `tonight` moved into the time family (G2 #9) — it's no longer
    // date-only, it now carries a concrete evening time.
    expect(parse('standup today').dueDate, '2026-06-06');
    final tonight = parse('cook tonight');
    expect(tonight.dueDate, '2026-06-06T20:00:00');
    expect(tonight.hasTime, isTrue);
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

  // ── G1: false-positive removal ──────────────────────────────────────────────
  //
  // Every one of these was verified against the PRE-G1 parser: it silently
  // set a field (or ate a word out of the title) on ordinary English that
  // had nothing to do with a date/priority. None of them should set any
  // field, and the title must come back byte-for-byte unchanged.

  group('anti-patterns (G1 false positives)', () {
    void assertNoMatch(String input) {
      final r = parse(input);
      expect(r.dueDate, isNull, reason: 'dueDate for "$input"');
      expect(r.priority, isNull, reason: 'priority for "$input"');
      expect(r.project, isNull, reason: 'project for "$input"');
      expect(r.recurrence, isNull, reason: 'recurrence for "$input"');
      expect(r.tokens, isEmpty, reason: 'tokens for "$input"');
      expect(r.cleanTitle, input, reason: 'cleanTitle for "$input"');
    }

    test('sat down with the team (bare "sat" is not a date)', () {
      assertNoMatch('sat down with the team');
    });

    test(
      'fix the sat nav (bare "sat" is not a date, and stays in the title)',
      () {
        assertNoMatch('fix the sat nav');
      },
    );

    test('sun is out (bare "sun" is not a date)', () {
      assertNoMatch('sun is out');
    });

    test('wed the bride (bare "wed" is not a date)', () {
      assertNoMatch('wed the bride');
    });

    test('my p1 project (bare priority code removed)', () {
      assertNoMatch('my p1 project');
    });

    test('ship ! now (single bang is not a priority token)', () {
      assertNoMatch('ship ! now');
    });

    test('split 1/2 of the cost (single-digit fraction is not a date)', () {
      assertNoMatch('split 1/2 of the cost');
    });

    test('read chapter 3/4 (single-digit fraction is not a date)', () {
      assertNoMatch('read chapter 3/4');
    });

    test('aspect ratio 16/9 (invalid month keeps this a non-match)', () {
      assertNoMatch('aspect ratio 16/9');
    });
  });

  group('weekday false positives are fixed by a required cue', () {
    test('mon/tue/thu/fri and full names stay bare (unaffected by G1)', () {
      expect(parse('call mon').dueDate, '2026-06-08');
      expect(parse('call saturday').dueDate, '2026-06-06');
    });
  });

  group('weekday cue disambiguation (G1 #3 / G2 #6)', () {
    test('a preceding cue word ("on") lets a restricted weekday count', () {
      final r = parse('call on sat');
      expect(r.dueDate, '2026-06-06');
      // "on" is deliberately NOT absorbed into the token (G2 ships without
      // on/from — see the cued-weekday group below) so it stays in the title.
      expect(r.cleanTitle, 'call on');
    });

    test('an adjacent clock token after the weekday counts as a cue', () {
      final r = parse('sat 5pm');
      expect(r.dueDate, '2026-06-06T17:00:00');
      expect(r.hasTime, isTrue);
    });

    test('an adjacent clock token before the weekday counts as a cue', () {
      final r = parse('5pm sat');
      expect(r.dueDate, '2026-06-06T17:00:00');
      expect(r.hasTime, isTrue);
    });

    test('being the entire input counts as a cue', () {
      final r = parse('sat');
      expect(r.dueDate, '2026-06-06');
      expect(r.cleanTitle, '');
    });

    test('without any cue, the restricted weekday stays a false negative', () {
      final r = parse('the sat show');
      expect(r.dueDate, isNull);
      expect(r.cleanTitle, 'the sat show');
    });
  });

  group('_mdDate hardening (G1 #4)', () {
    test('a two-digit component next to a cue word is still rejected', () {
      // "page" precedes a genuinely 2-digit-having M/D pair — without the
      // lookbehind this would parse as a valid (if wrong) date.
      final r = parse('page 12/31');
      expect(r.dueDate, isNull);
      expect(r.cleanTitle, 'page 12/31');
    });

    test(
      'pinned: report 6/10 and bare 12/31 still resolve (regression guard)',
      () {
        expect(parse('report 6/10').dueDate, '2026-06-10');
        expect(parse('12/31').dueDate, '2026-12-31');
      },
    );
  });

  group('day after tomorrow / overmorrow (G1 #5)', () {
    test('"day after tomorrow" resolves to today+2, not plain tomorrow', () {
      final r = parse('day after tomorrow');
      expect(r.dueDate, '2026-06-08');
      expect(r.cleanTitle, '');
    });

    test('"overmorrow" resolves to today+2', () {
      final r = parse('overmorrow');
      expect(r.dueDate, '2026-06-08');
      expect(r.cleanTitle, '');
    });

    test('embedded in a sentence, the whole phrase is stripped', () {
      final r = parse('submit report day after tomorrow');
      expect(r.cleanTitle, 'submit report');
      expect(r.dueDate, '2026-06-08');
    });
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

    test('+Nw and +N weeks both add N*7 days (G3)', () {
      expect(parse('renew +2w').dueDate, '2026-06-20');
      expect(parse('renew +2 weeks').dueDate, '2026-06-20');
    });

    test('+Nm and +N months both add calendar months (G3)', () {
      expect(parse('renew +1m').dueDate, '2026-07-06');
      expect(parse('renew +1 month').dueDate, '2026-07-06');
      expect(parse('renew +2 months').dueDate, '2026-08-06');
    });

    test('in N months offsets by calendar months (G3)', () {
      final r = parse('ping in 3 months');
      expect(r.cleanTitle, 'ping');
      expect(r.dueDate, '2026-09-06');
    });

    test(
      'a month-add clamps day-of-month instead of rolling into a later month',
      () {
        // Jan 31 + 1 month must land on Feb 28 (2026 is not a leap year), NOT
        // silently roll forward into March the way DateTime(y, m+1, d) would
        // on its own.
        final r = parseSmartAdd('renew +1 month', now: DateTime(2026, 1, 31));
        expectWellFormedSpans('renew +1 month', r);
        expect(r.dueDate, '2026-02-28');
      },
    );

    test('a month-add respects a leap-year February', () {
      final r = parseSmartAdd('renew +1m', now: DateTime(2024, 1, 31));
      expectWellFormedSpans('renew +1m', r);
      expect(r.dueDate, '2024-02-29');
    });

    test('a month-add rolls the year forward past December', () {
      final r = parseSmartAdd('renew +13m', now: DateTime(2026, 1, 15));
      expectWellFormedSpans('renew +13m', r);
      expect(r.dueDate, '2027-02-15');
    });
  });

  group('in N minutes (G3)', () {
    test('sets a time of now + N minutes', () {
      final input = 'ping in 20 minutes';
      final r = parseSmartAdd(input, now: DateTime(2026, 6, 6, 14, 30));
      expectWellFormedSpans(input, r);
      expect(r.cleanTitle, 'ping');
      expect(r.dueDate, '2026-06-06T14:50:00');
      expect(r.hasTime, isTrue);
    });

    test('"min" and "mins" both work', () {
      expect(
        parseSmartAdd(
          'ping in 5 min',
          now: DateTime(2026, 6, 6, 14, 30),
        ).dueDate,
        '2026-06-06T14:35:00',
      );
      expect(
        parseSmartAdd(
          'ping in 5 mins',
          now: DateTime(2026, 6, 6, 14, 30),
        ).dueDate,
        '2026-06-06T14:35:00',
      );
    });

    test('rolls the date forward when it crosses midnight', () {
      final input = 'call in 90 minutes';
      final r = parseSmartAdd(input, now: DateTime(2026, 6, 6, 23, 30));
      expectWellFormedSpans(input, r);
      expect(r.dueDate, '2026-06-07T01:00:00');
    });

    test(
      'bare "m" is NOT a minutes shorthand (avoids the +Nm=months clash)',
      () {
        final r = parse('ping in 3m');
        expect(r.dueDate, isNull);
        expect(r.cleanTitle, 'ping in 3m');
      },
    );

    test('"in 3 months" is never swallowed by the minutes matcher', () {
      final r = parse('ping in 3 months');
      expect(r.tokens.length, 1);
      expect(r.tokens.single.kind, SmartTokenKind.date);
      expect(r.dueDate, '2026-09-06');
    });
  });

  group('eom / eoy (G3)', () {
    test('eom is the last day of the current month', () {
      expect(parse('wrap eom').dueDate, '2026-06-30');
    });

    test('eoy is Dec 31 of the current year', () {
      expect(parse('wrap eoy').dueDate, '2026-12-31');
    });

    test('abbreviations never match mid-word', () {
      final r = parse('the eomish plan is eoyish too');
      expect(r.dueDate, isNull);
      expect(r.cleanTitle, 'the eomish plan is eoyish too');
    });
  });

  group('midday (G3)', () {
    test('midday resolves to 12:00, same as noon', () {
      expect(parse('a midday').dueDate, '2026-06-06T12:00:00');
    });
  });

  group('weekday vocabulary fill-in (G3: tues/thur/thurs/weds)', () {
    test(
      'tues / thur / thurs resolve like their base short forms (bare, no cue needed)',
      () {
        expect(parse('call tues').dueDate, '2026-06-09'); // upcoming Tuesday
        expect(parse('call thur').dueDate, '2026-06-11'); // upcoming Thursday
        expect(parse('call thurs').dueDate, '2026-06-11');
      },
    );

    test('"weds" joins the restricted tier (present tense of "to wed")', () {
      // Same collision class as bare "wed" -- a plain sentence must not
      // pick it up as a date.
      final r = parse('she weds him tomorrow');
      final dateTokens = r.tokens
          .where((t) => t.kind == SmartTokenKind.date)
          .toList();
      expect(dateTokens.length, 1);
      expect(
        'she weds him tomorrow'.substring(
          dateTokens.single.start,
          dateTokens.single.end,
        ),
        'tomorrow',
      );
      expect(r.cleanTitle, 'she weds him');
    });

    test('"weds" resolves once disambiguated by a cue', () {
      final r = parse('due weds');
      expect(r.cleanTitle, '');
      expect(r.dueDate, '2026-06-10'); // upcoming Wednesday
    });

    test('bare "weds" as the sole input resolves too', () {
      final r = parse('weds');
      expect(r.dueDate, '2026-06-10');
      expect(r.cleanTitle, '');
    });

    test('next/every absorb the new short forms too', () {
      expect(parse('demo next weds').dueDate, '2026-06-17');
      final r = parse('standup every thurs');
      expect(r.recurrence?.weekday, DateTime.thursday);
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
      // G2 #8: "tom morning" is now recognized as ONE absorbed time token
      // (not two separate date+time tokens) — the time-family callback
      // re-derives the "tom" cue and carries the day forward via `timeDate`
      // so this still resolves to TOMORROW, not today.
      final r = parse('standup tom morning');
      expect(r.cleanTitle, 'standup');
      expect(r.dueDate, '2026-06-07T09:00:00');
      expect(r.hasTime, isTrue);
      expect(r.tokens.length, 1);
      expect(r.tokens.single.kind, SmartTokenKind.time);
    });

    test('"tomorrow morning" (unabbreviated) also carries the day forward', () {
      final r = parse('call tomorrow morning');
      expect(r.cleanTitle, 'call');
      expect(r.dueDate, '2026-06-07T09:00:00');
      expect(r.hasTime, isTrue);
    });

    test(
      'a bare cue-less day token + keyword still combine (5pm sat morning-style composition unaffected)',
      () {
        // Sanity check that ordinary two-token composition (date token +
        // separate time token) still works when the time keyword has no
        // absorbable cue in front of it.
        final r = parse('call mon morning');
        expect(r.cleanTitle, 'call');
        expect(r.dueDate, '2026-06-08T09:00:00');
        expect(r.hasTime, isTrue);
      },
    );

    test('"this morning" absorbs the "this" cue into the title too', () {
      // G2 #8: previously only "morning" was recognized, stranding "this".
      final r = parse('wake up this morning');
      expect(r.cleanTitle, 'wake up');
      expect(r.dueDate, '2026-06-06T09:00:00');
      expect(r.hasTime, isTrue);
    });

    test(
      '"tonight" now resolves to today at 20:00 (moved into the time family, G2 #9)',
      () {
        final r = parse('cook tonight');
        expect(r.dueDate, '2026-06-06T20:00:00');
        expect(r.hasTime, isTrue);
        expect(r.cleanTitle, 'cook');
      },
    );
  });

  group('cue absorption around clock times (G2 #7)', () {
    test(
      '"at" in front of a 12-hour clock is absorbed into the title cleanup',
      () {
        final r = parse('call at 5pm');
        expect(r.cleanTitle, 'call');
        expect(r.dueDate, '2026-06-06T17:00:00');
        expect(r.hasTime, isTrue);
      },
    );

    test(
      '"at" in front of a 24-hour clock is absorbed into the title cleanup',
      () {
        final r = parse('meeting at 17:00');
        expect(r.cleanTitle, 'meeting');
        expect(r.dueDate, '2026-06-06T17:00:00');
        expect(r.hasTime, isTrue);
      },
    );

    test('no "at" present -> unaffected (regression guard)', () {
      final r = parse('meeting 17:00');
      expect(r.cleanTitle, 'meeting');
      expect(r.dueDate, '2026-06-06T17:00:00');
    });
  });

  group('cued-weekday absorption (G2 #6)', () {
    test('"by wed" absorbs the cue and resolves to the upcoming Wednesday', () {
      final r = parse('meet by wed');
      expect(r.cleanTitle, 'meet');
      expect(r.dueDate, '2026-06-10');
    });

    test('"due mon" absorbs the cue too (not just the restricted trio)', () {
      final r = parse('due mon');
      expect(r.cleanTitle, '');
      expect(r.dueDate, '2026-06-08');
    });

    test('"coming sat" absorbs the cue', () {
      final r = parse('coming sat');
      expect(r.cleanTitle, '');
      expect(r.dueDate, '2026-06-06');
    });

    test('"on"/"from" are deliberately NOT absorbed (deferred cue words)', () {
      // "turn on monday" must not eat "on" -- that's ordinary English, not a
      // date cue. The bare weekday still resolves (unrestricted "monday"),
      // but "on" stays in the title.
      final r = parse('turn on monday');
      expect(r.dueDate, '2026-06-08');
      expect(r.cleanTitle, 'turn on');
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

  group('bare priority codes (removed — G1 #1)', () {
    // Bare `p1..p4` was deleted: `my p1 project` used to read as "urgent
    // today", a pure false positive with no upside since `!p1` already
    // covers the same intent unambiguously. These are now near-misses.
    test('p1..p4 no longer set a priority', () {
      expect(parse('a p1').priority, isNull);
      expect(parse('a p2').priority, isNull);
      expect(parse('a p3').priority, isNull);
      expect(parse('a p4').priority, isNull);
    });

    test('bare priority is left in the title untouched', () {
      final r = parse('upgrade plan p2');
      expect(r.cleanTitle, 'upgrade plan p2');
      expect(r.priority, isNull);
    });

    test('bare P3 (any case) no longer matches', () {
      expect(parse('a P3').priority, isNull);
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
      expect(r.tokens.map((t) => input.substring(t.start, t.end)).toList(), [
        'tomorrow',
        '5pm',
      ]);
      expect(r.tokens.map((t) => t.kind).toList(), [
        SmartTokenKind.date,
        SmartTokenKind.time,
      ]);
    });

    test('spans are sorted by start and never overlap', () {
      // `parse()` already runs `expectWellFormedSpans` (ascending,
      // non-overlapping, in-bounds); this test pins the additional,
      // fixture-specific project-token assertion below.
      final r = parse('plan next fri 9am p2 #work later #ignored');
      // Only the FIRST project token is recognized.
      final projects = r.tokens
          .where((t) => t.kind == SmartTokenKind.project)
          .toList();
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
