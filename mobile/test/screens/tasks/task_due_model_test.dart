// Pure tests for the due-date / reminder derivations. These rules previously
// lived as `State` getters and could only be reached by driving the whole
// detail sheet — see `detail_sheet_reminder_preserve_test.dart` for the
// (still-passing) widget-level regression suite this complements.

import 'package:flutter/material.dart' show TimeOfDay;
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/reminder_lead.dart';
import 'package:lazyclaw_mobile/screens/settings/settings_prefs.dart'
    show kDefaultReminderLead;
import 'package:lazyclaw_mobile/screens/tasks/task_due_model.dart';

TaskDueModel model({
  String? dueDay,
  TimeOfDay? dueTime,
  String? originalReminderAt,
  ReminderLead? explicitLead,
  ReminderLead defaultLead = kDefaultReminderLead,
  bool reminderTouched = false,
  bool dueTouched = false,
  DateTime? now,
}) => TaskDueModel(
  dueDay: dueDay,
  dueTime: dueTime,
  originalReminderAt: originalReminderAt,
  explicitLead: explicitLead,
  defaultLead: defaultLead,
  reminderTouched: reminderTouched,
  dueTouched: dueTouched,
  now: now,
);

void main() {
  group('composedDue', () {
    test('nothing set → null', () {
      expect(model().composedDue, isNull);
    });

    test('a day alone stays DATE-ONLY', () {
      expect(model(dueDay: '2026-06-10').composedDue, '2026-06-10');
    });

    test('a day + time compose into a datetime', () {
      final out = model(
        dueDay: '2026-06-10',
        dueTime: const TimeOfDay(hour: 9, minute: 30),
      ).composedDue;
      expect(out, startsWith('2026-06-10'));
      expect(out, contains('09:30'));
    });

    test('a time with NO day anchors onto today', () {
      final out = model(
        dueTime: const TimeOfDay(hour: 8, minute: 0),
        now: DateTime(2026, 8, 3),
      ).composedDue;
      expect(out, startsWith('2026-08-03'));
    });

    test('an unparseable day is passed through rather than throwing', () {
      expect(model(dueDay: 'garbage').composedDue, 'garbage');
    });
  });

  group('effectiveLead', () {
    test('an explicit choice wins over the global default', () {
      expect(
        model(
          explicitLead: ReminderLead.none,
          defaultLead: ReminderLead.min30,
        ).effectiveLead,
        ReminderLead.none,
      );
    });

    test('falls back to the global default', () {
      expect(
        model(defaultLead: ReminderLead.min30).effectiveLead,
        ReminderLead.min30,
      );
    });
  });

  group('survivingReminderAt', () {
    const stored = '2026-06-10T08:00:00';

    test('touching the REMIND control is an explicit clear', () {
      expect(
        model(
          dueDay: '2026-06-10',
          originalReminderAt: stored,
          reminderTouched: true,
        ).survivingReminderAt,
        isNull,
      );
    });

    test('nothing survives when there was no reminder', () {
      expect(model(dueDay: '2026-06-10').survivingReminderAt, isNull);
    });

    test('removing the due date orphans the reminder — it must not survive and '
        'keep nagging about nothing', () {
      expect(model(originalReminderAt: stored).survivingReminderAt, isNull);
    });

    test('an untouched due day preserves the stored instant verbatim', () {
      expect(
        model(
          dueDay: '2026-06-10',
          originalReminderAt: stored,
        ).survivingReminderAt,
        stored,
      );
    });

    test('moving the day re-anchors the SAME clock time onto the new day', () {
      final out = model(
        dueDay: '2026-06-12',
        originalReminderAt: stored,
        dueTouched: true,
      ).survivingReminderAt;
      expect(out, startsWith('2026-06-12'));
      expect(out, contains('08:00'));
    });
  });

  group('reminderArg (the three-way Save rule)', () {
    test('a TIMED due makes the lead picker authoritative', () {
      final m = model(
        dueDay: '2026-06-10',
        dueTime: const TimeOfDay(hour: 9, minute: 0),
        explicitLead: ReminderLead.none,
      );
      expect(m.reminderArg, m.composedReminderAt);
    });

    test(
      'a DATE-ONLY due with a surviving, UNCHANGED reminder writes nothing — '
      'this is the fix for reminders being deleted by an unrelated edit',
      () {
        expect(
          model(
            dueDay: '2026-06-10',
            originalReminderAt: '2026-06-10T08:00:00',
          ).reminderArg,
          isNull,
        );
      },
    );

    test('a DATE-ONLY due with nothing surviving force-clears with ""', () {
      expect(
        model(
          dueDay: '2026-06-10',
          originalReminderAt: '2026-06-10T08:00:00',
          reminderTouched: true,
        ).reminderArg,
        '',
      );
    });

    test('a re-anchored reminder is written as the new instant', () {
      final out = model(
        dueDay: '2026-06-12',
        originalReminderAt: '2026-06-10T08:00:00',
        dueTouched: true,
      ).reminderArg;
      expect(out, startsWith('2026-06-12'));
    });
  });
}
