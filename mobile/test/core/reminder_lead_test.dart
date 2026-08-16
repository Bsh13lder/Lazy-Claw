// Unit tests for the pure reminder lead-time model + math in
// core/reminder_lead.dart. Plain Dart (no plugin, no binding).

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/reminder_lead.dart';

void main() {
  group('computeReminderAt', () {
    const due = '2026-06-07T17:00:00';

    test('At time → the due instant itself', () {
      expect(
        computeReminderAt(dueDate: due, lead: ReminderLead.atTime),
        DateTime(2026, 6, 7, 17, 0, 0),
      );
    });

    test('10 min before → due − 10m', () {
      expect(
        computeReminderAt(dueDate: due, lead: ReminderLead.min10),
        DateTime(2026, 6, 7, 16, 50, 0),
      );
    });

    test('30 min before → due − 30m', () {
      expect(
        computeReminderAt(dueDate: due, lead: ReminderLead.min30),
        DateTime(2026, 6, 7, 16, 30, 0),
      );
    });

    test('1 hour before → due − 1h', () {
      expect(
        computeReminderAt(dueDate: due, lead: ReminderLead.hour1),
        DateTime(2026, 6, 7, 16, 0, 0),
      );
    });

    test('1 day before → due − 1d', () {
      expect(
        computeReminderAt(dueDate: due, lead: ReminderLead.day1),
        DateTime(2026, 6, 6, 17, 0, 0),
      );
    });

    test('custom 45 min before → due − 45m', () {
      expect(
        computeReminderAt(
          dueDate: due,
          lead: const ReminderLead(Duration(minutes: 45)),
        ),
        DateTime(2026, 6, 7, 16, 15, 0),
      );
    });

    test('None → null (no schedule)', () {
      expect(computeReminderAt(dueDate: due, lead: ReminderLead.none), isNull);
    });

    test('date-only due → null (never invents a time)', () {
      expect(
        computeReminderAt(dueDate: '2026-06-07', lead: ReminderLead.min30),
        isNull,
      );
    });

    test('null due → null', () {
      expect(
        computeReminderAt(dueDate: null, lead: ReminderLead.min30),
        isNull,
      );
    });

    test('unparseable timed due → null', () {
      expect(
        computeReminderAt(dueDate: 'garbageT??', lead: ReminderLead.min30),
        isNull,
      );
    });

    test('a 1-day lead can land in the past — still computed (filter happens '
        'at schedule time, not here)', () {
      final at =
          computeReminderAt(dueDate: due, lead: ReminderLead.day1);
      expect(at, DateTime(2026, 6, 6, 17, 0, 0));
    });
  });

  group('leadFromReminderAt (display derivation)', () {
    const due = '2026-06-07T17:00:00';

    test('no reminderAt → None', () {
      expect(leadFromReminderAt(due, null), ReminderLead.none);
      expect(leadFromReminderAt(due, ''), ReminderLead.none);
    });

    test('reminder == due → At time', () {
      expect(leadFromReminderAt(due, '2026-06-07T17:00:00'),
          ReminderLead.atTime);
    });

    test('30m before → min30 preset', () {
      expect(leadFromReminderAt(due, '2026-06-07T16:30:00'),
          ReminderLead.min30);
    });

    test('1 day before → day1 preset', () {
      expect(leadFromReminderAt(due, '2026-06-06T17:00:00'),
          ReminderLead.day1);
    });

    test('45m before → custom (non-preset)', () {
      final lead = leadFromReminderAt(due, '2026-06-07T16:15:00');
      expect(lead.isCustom, isTrue);
      expect(lead.lead, const Duration(minutes: 45));
    });

    test('reminder AFTER due → At time (clamped, never negative)', () {
      expect(leadFromReminderAt(due, '2026-06-07T18:00:00'),
          ReminderLead.atTime);
    });

    test('date-only due → None (cannot express a lead)', () {
      expect(leadFromReminderAt('2026-06-07', '2026-06-07T16:30:00'),
          ReminderLead.none);
    });
  });

  group('round-trip lead → reminderAt → leadFromReminderAt', () {
    const due = '2026-06-07T17:00:00';

    for (final lead in const [
      ReminderLead.atTime,
      ReminderLead.min10,
      ReminderLead.min30,
      ReminderLead.hour1,
      ReminderLead.day1,
      ReminderLead(Duration(minutes: 45)),
      ReminderLead(Duration(hours: 3)),
      ReminderLead(Duration(days: 2)),
    ]) {
      test('round-trips ${lead.label}', () {
        final at = computeReminderAt(dueDate: due, lead: lead);
        final iso = formatReminderIso(at!);
        expect(leadFromReminderAt(due, iso), lead);
      });
    }
  });

  group('resolveReminderAt (persistence + global default)', () {
    const due = '2026-06-07T17:00:00';

    test('explicit lead wins → due − lead', () {
      final r = resolveReminderAt(
        dueDate: due,
        explicitLead: ReminderLead.hour1,
        defaultLead: ReminderLead.min30,
      );
      expect(r, '2026-06-07T16:00:00');
    });

    test('untouched picker (null) → global default applies', () {
      final r = resolveReminderAt(
        dueDate: due,
        explicitLead: null,
        defaultLead: ReminderLead.min30,
      );
      expect(r, '2026-06-07T16:30:00');
    });

    test('explicit None → cleared (empty string)', () {
      final r = resolveReminderAt(
        dueDate: due,
        explicitLead: ReminderLead.none,
        defaultLead: ReminderLead.min30,
      );
      expect(r, '');
    });

    test('date-only due → cleared, default never applied', () {
      final r = resolveReminderAt(
        dueDate: '2026-06-07',
        explicitLead: null,
        defaultLead: ReminderLead.min30,
      );
      expect(r, '');
    });

    test('default None + untouched → cleared', () {
      final r = resolveReminderAt(
        dueDate: due,
        explicitLead: null,
        defaultLead: ReminderLead.none,
      );
      expect(r, '');
    });
  });

  group('ReminderLead value semantics', () {
    test('value equality is duration-based', () {
      expect(const ReminderLead(Duration(minutes: 10)), ReminderLead.min10);
      expect(ReminderLead.none, const ReminderLead(null));
      expect(ReminderLead.atTime, const ReminderLead(Duration.zero));
    });

    test('isNone / isCustom classification', () {
      expect(ReminderLead.none.isNone, isTrue);
      expect(ReminderLead.min30.isNone, isFalse);
      expect(ReminderLead.min30.isCustom, isFalse);
      expect(ReminderLead.atTime.isCustom, isFalse);
      expect(const ReminderLead(Duration(minutes: 45)).isCustom, isTrue);
      // A custom value equal to a preset is canonicalised to "not custom".
      expect(const ReminderLead(Duration(minutes: 10)).isCustom, isFalse);
    });

    test('labels', () {
      expect(ReminderLead.none.label, 'None');
      expect(ReminderLead.atTime.label, 'At time');
      expect(ReminderLead.min10.label, '10 min before');
      expect(ReminderLead.min30.label, '30 min before');
      expect(ReminderLead.hour1.label, '1 hour before');
      expect(ReminderLead.day1.label, '1 day before');
      expect(const ReminderLead(Duration(days: 2)).label, '2 days before');
      expect(const ReminderLead(Duration(hours: 3)).label, '3 hours before');
      expect(const ReminderLead(Duration(minutes: 45)).label, '45 min before');
    });
  });

  group('ReminderLeadUnit + decomposeCustomLead', () {
    test('unit → duration', () {
      expect(ReminderLeadUnit.minutes.toDuration(20), const Duration(minutes: 20));
      expect(ReminderLeadUnit.hours.toDuration(3), const Duration(hours: 3));
      expect(ReminderLeadUnit.days.toDuration(2), const Duration(days: 2));
    });

    test('decompose picks the natural unit', () {
      expect(decomposeCustomLead(const Duration(minutes: 45)),
          (value: 45, unit: ReminderLeadUnit.minutes));
      expect(decomposeCustomLead(const Duration(hours: 3)),
          (value: 3, unit: ReminderLeadUnit.hours));
      expect(decomposeCustomLead(const Duration(days: 2)),
          (value: 2, unit: ReminderLeadUnit.days));
      // 90 min is not a whole hour → stays minutes.
      expect(decomposeCustomLead(const Duration(minutes: 90)),
          (value: 90, unit: ReminderLeadUnit.minutes));
    });
  });

  group('leadRepresentsReminder', () {
    test('true for a clean 30-min lead pair', () {
      expect(
        leadRepresentsReminder('2026-06-10T17:00:00', '2026-06-10T16:30:00'),
        isTrue,
      );
    });

    test('true for an at-time pair', () {
      expect(
        leadRepresentsReminder('2026-06-10T17:00:00', '2026-06-10T17:00:00'),
        isTrue,
      );
    });

    test('FALSE for a reminder AFTER its due (negative lead)', () {
      // The midnight-due / late-night-reminder recurring chain — the shape
      // whose coerced "At time" save caused the 2026-07-31 past-fire.
      expect(
        leadRepresentsReminder('2026-06-10T00:00:00', '2026-06-10T23:30:00'),
        isFalse,
      );
    });

    test('true when there is nothing to lose', () {
      expect(leadRepresentsReminder('2026-06-10T17:00:00', null), isTrue);
      expect(leadRepresentsReminder('2026-06-10T17:00:00', ''), isTrue);
      expect(
        leadRepresentsReminder('2026-06-10', '2026-06-10T09:00:00'),
        isTrue, // date-only due: the surviving-reminder path owns preservation
      );
    });
  });
}
