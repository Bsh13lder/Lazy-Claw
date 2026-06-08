// Unit tests for the pure recurrence model + 5-field cron mapping.
//
// Cron is standard `minute hour day-of-month month day-of-week`, with
// day-of-week Sun=0 … Sat=6 (croniter / POSIX). Dart weekday is Mon=1 … Sun=7.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/recurrence.dart';

void main() {
  // A Monday 17:00 anchor (2026-06-08 is a Monday).
  final mondayAt17 = DateTime(2026, 6, 8, 17, 0);
  // A Wednesday 09:30 anchor (2026-06-10 is a Wednesday).
  final wedAt0930 = DateTime(2026, 6, 10, 9, 30);

  group('recurrenceToCron', () {
    test('none → null', () {
      expect(recurrenceToCron(Recurrence.none), isNull);
    });

    test('custom → null', () {
      expect(recurrenceToCron(Recurrence.custom), isNull);
    });

    test('daily uses the anchor clock', () {
      expect(
        recurrenceToCron(Recurrence.daily, dueAnchor: mondayAt17),
        '0 17 * * *',
      );
    });

    test('daily with no anchor uses the default 09:00', () {
      expect(recurrenceToCron(Recurrence.daily), '0 9 * * *');
    });

    test('daily honours custom default time', () {
      expect(
        recurrenceToCron(Recurrence.daily, defaultHour: 8, defaultMinute: 15),
        '15 8 * * *',
      );
    });

    test('weekdays → Mon-Fri range', () {
      expect(
        recurrenceToCron(Recurrence.weekdays, dueAnchor: wedAt0930),
        '30 9 * * 1-5',
      );
    });

    test('weekly on Monday (explicit weekday) → dow 1', () {
      final r = Recurrence(RecurrenceKind.weekly, weekday: DateTime.monday);
      expect(recurrenceToCron(r, dueAnchor: mondayAt17), '0 17 * * 1');
    });

    test('weekly on Sunday (Dart 7) → cron dow 0', () {
      final r = Recurrence(RecurrenceKind.weekly, weekday: DateTime.sunday);
      expect(recurrenceToCron(r, dueAnchor: mondayAt17), '0 17 * * 0');
    });

    test('weekly with no explicit weekday derives dow from the anchor', () {
      // Wednesday anchor → cron dow 3.
      expect(
        recurrenceToCron(Recurrence.weekly, dueAnchor: wedAt0930),
        '30 9 * * 3',
      );
    });

    test('weekly with no anchor and no weekday defaults to Monday (dow 1)', () {
      expect(recurrenceToCron(Recurrence.weekly), '0 9 * * 1');
    });

    test('monthly → anchor day-of-month', () {
      expect(
        recurrenceToCron(Recurrence.monthly, dueAnchor: mondayAt17),
        '0 17 8 * *',
      );
    });

    test('monthly with no anchor → day 1', () {
      expect(recurrenceToCron(Recurrence.monthly), '0 9 1 * *');
    });

    test('yearly → anchor day + month', () {
      expect(
        recurrenceToCron(Recurrence.yearly, dueAnchor: mondayAt17),
        '0 17 8 6 *',
      );
    });

    test('yearly with no anchor → Jan 1', () {
      expect(recurrenceToCron(Recurrence.yearly), '0 9 1 1 *');
    });
  });

  group('recurrenceFromCron', () {
    test('null / blank → none', () {
      expect(recurrenceFromCron(null), Recurrence.none);
      expect(recurrenceFromCron(''), Recurrence.none);
      expect(recurrenceFromCron('   '), Recurrence.none);
    });

    test('daily', () {
      expect(recurrenceFromCron('0 17 * * *'), Recurrence.daily);
    });

    test('weekdays', () {
      expect(recurrenceFromCron('30 9 * * 1-5'), Recurrence.weekdays);
    });

    test('weekly on Monday → weekday 1', () {
      final r = recurrenceFromCron('0 17 * * 1');
      expect(r.kind, RecurrenceKind.weekly);
      expect(r.weekday, DateTime.monday);
    });

    test('weekly on Sunday (cron dow 0) → Dart weekday 7', () {
      final r = recurrenceFromCron('0 17 * * 0');
      expect(r.kind, RecurrenceKind.weekly);
      expect(r.weekday, DateTime.sunday);
    });

    test('monthly', () {
      expect(recurrenceFromCron('0 17 8 * *'), Recurrence.monthly);
    });

    test('yearly', () {
      expect(recurrenceFromCron('0 17 8 6 *'), Recurrence.yearly);
    });

    test('unknown shape (wrong field count) → custom', () {
      expect(recurrenceFromCron('0 17 * *'), Recurrence.custom);
      expect(recurrenceFromCron('0 17 * * * *'), Recurrence.custom);
    });

    test('non-numeric clock fields → custom', () {
      expect(recurrenceFromCron('*/5 * * * *'), Recurrence.custom);
    });

    test('multi-value day-of-week list → custom', () {
      expect(recurrenceFromCron('0 9 * * 1,3,5'), Recurrence.custom);
    });

    test('out-of-range monthly day → custom', () {
      expect(recurrenceFromCron('0 9 40 * *'), Recurrence.custom);
    });
  });

  group('round-trip (toCron → fromCron)', () {
    final anchors = {
      'daily': (Recurrence.daily, mondayAt17),
      'weekdays': (Recurrence.weekdays, wedAt0930),
      'monthly': (Recurrence.monthly, mondayAt17),
      'yearly': (Recurrence.yearly, mondayAt17),
    };

    anchors.forEach((name, pair) {
      test('$name survives a round-trip', () {
        final cron = recurrenceToCron(pair.$1, dueAnchor: pair.$2);
        expect(recurrenceFromCron(cron).kind, pair.$1.kind);
      });
    });

    test('weekly round-trips kind AND weekday', () {
      for (final wd in [
        DateTime.monday,
        DateTime.thursday,
        DateTime.saturday,
        DateTime.sunday,
      ]) {
        final r = Recurrence(RecurrenceKind.weekly, weekday: wd);
        final cron = recurrenceToCron(r, dueAnchor: mondayAt17);
        final back = recurrenceFromCron(cron);
        expect(back.kind, RecurrenceKind.weekly, reason: 'cron=$cron');
        expect(back.weekday, wd, reason: 'cron=$cron');
      }
    });
  });

  group('recurrenceLabel', () {
    test('labels each kind', () {
      expect(recurrenceLabel(Recurrence.none), 'Does not repeat');
      expect(recurrenceLabel(Recurrence.daily), 'Daily');
      expect(recurrenceLabel(Recurrence.weekdays), 'Weekdays');
      expect(recurrenceLabel(Recurrence.monthly), 'Monthly');
      expect(recurrenceLabel(Recurrence.yearly), 'Yearly');
      expect(recurrenceLabel(Recurrence.custom), 'Repeats');
    });

    test('weekly carries the weekday abbreviation', () {
      expect(
        recurrenceLabel(
          Recurrence(RecurrenceKind.weekly, weekday: DateTime.monday),
        ),
        'Weekly (Mon)',
      );
      expect(
        recurrenceLabel(
          Recurrence(RecurrenceKind.weekly, weekday: DateTime.sunday),
        ),
        'Weekly (Sun)',
      );
    });

    test('weekly with no weekday → plain "Weekly"', () {
      expect(recurrenceLabel(Recurrence.weekly), 'Weekly');
    });
  });

  group('cronChipLabel', () {
    test('null cron → null (no chip)', () {
      expect(cronChipLabel(null), isNull);
      expect(cronChipLabel(''), isNull);
    });

    test('known cron → its label', () {
      expect(cronChipLabel('0 17 * * 1'), 'Weekly (Mon)');
      expect(cronChipLabel('0 9 * * *'), 'Daily');
    });

    test('custom cron → "Repeats"', () {
      expect(cronChipLabel('*/5 * * * *'), 'Repeats');
    });
  });

  group('recurrenceAnchorFromDue', () {
    test('null / blank → null', () {
      expect(recurrenceAnchorFromDue(null), isNull);
      expect(recurrenceAnchorFromDue(''), isNull);
    });

    test('a timed due keeps its clock', () {
      final a = recurrenceAnchorFromDue('2026-06-08T17:30:00');
      expect(a, DateTime(2026, 6, 8, 17, 30));
    });

    test('a date-only due defaults the clock to 09:00, keeps the date', () {
      final a = recurrenceAnchorFromDue('2026-06-10');
      expect(a, DateTime(2026, 6, 10, 9, 0));
    });

    test('date-only honours a custom default time', () {
      final a = recurrenceAnchorFromDue(
        '2026-06-10',
        defaultHour: 8,
        defaultMinute: 15,
      );
      expect(a, DateTime(2026, 6, 10, 8, 15));
    });

    test('a date-only due → daily cron at the default time (not midnight)', () {
      final anchor = recurrenceAnchorFromDue('2026-06-10');
      expect(
        recurrenceToCron(Recurrence.daily, dueAnchor: anchor),
        '0 9 * * *',
      );
    });

    test('a timed due → daily cron at the due time', () {
      final anchor = recurrenceAnchorFromDue('2026-06-10T18:45:00');
      expect(
        recurrenceToCron(Recurrence.daily, dueAnchor: anchor),
        '45 18 * * *',
      );
    });

    test('a date-only due → monthly cron keeps the day-of-month', () {
      final anchor = recurrenceAnchorFromDue('2026-06-10');
      expect(
        recurrenceToCron(Recurrence.monthly, dueAnchor: anchor),
        '0 9 10 * *',
      );
    });
  });

  group('Recurrence value semantics', () {
    test('equality is by kind + weekday', () {
      expect(
        const Recurrence(RecurrenceKind.weekly, weekday: 1),
        const Recurrence(RecurrenceKind.weekly, weekday: 1),
      );
      expect(
        const Recurrence(RecurrenceKind.weekly, weekday: 1),
        isNot(const Recurrence(RecurrenceKind.weekly, weekday: 2)),
      );
    });

    test('copyWith returns a new instance', () {
      final a = Recurrence.weekly;
      final b = a.copyWith(weekday: DateTime.friday);
      expect(b.weekday, DateTime.friday);
      expect(a.weekday, isNull, reason: 'original unchanged (immutability)');
    });

    test('repeats flag', () {
      expect(Recurrence.none.repeats, isFalse);
      expect(Recurrence.daily.repeats, isTrue);
    });
  });
}
