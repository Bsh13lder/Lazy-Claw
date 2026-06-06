// Unit tests for the on-device "Todoist-style" smart-add parser.
//
// `now` is pinned to Saturday 2026-06-06 so weekday / relative-date math is
// deterministic. The parser is pure Dart — no I/O, no LLM, no async.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/smart_add_parser.dart';

void main() {
  // Saturday, 6 June 2026.
  final now = DateTime(2026, 6, 6);
  ParsedTask parse(String s) => parseSmartAdd(s, now: now);

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

  test('bare time implies today (date only stored)', () {
    final r = parse('meeting 17:00');
    expect(r.cleanTitle, 'meeting');
    expect(r.dueDate, '2026-06-06');
  });

  test('am/pm time implies today', () {
    final r = parse('gym 6pm');
    expect(r.cleanTitle, 'gym');
    expect(r.dueDate, '2026-06-06');
  });

  test('when a weekday and a time both appear, the date wins', () {
    final r = parse('dentist tomorrow 9am');
    expect(r.cleanTitle, 'dentist');
    expect(r.dueDate, '2026-06-07'); // tomorrow, not today-from-9am
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
}
