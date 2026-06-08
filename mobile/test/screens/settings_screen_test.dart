import 'package:flutter_test/flutter_test.dart';

import 'package:lazyclaw_mobile/core/reminder_lead.dart';
import 'package:lazyclaw_mobile/screens/settings/settings_prefs.dart';

void main() {
  // ── SyncInterval ────────────────────────────────────────────────────────────

  group('SyncInterval', () {
    test('fromMinutes returns correct variant for each value', () {
      expect(SyncInterval.fromMinutes(0), SyncInterval.off);
      expect(SyncInterval.fromMinutes(15), SyncInterval.min15);
      expect(SyncInterval.fromMinutes(30), SyncInterval.min30);
      expect(SyncInterval.fromMinutes(60), SyncInterval.hour1);
    });

    test('fromMinutes falls back to 30 min for unknown value', () {
      expect(SyncInterval.fromMinutes(999), SyncInterval.min30);
      expect(SyncInterval.fromMinutes(-1), SyncInterval.min30);
    });

    test('every variant has a non-empty label', () {
      for (final v in SyncInterval.values) {
        expect(v.label, isNotEmpty, reason: '$v has empty label');
      }
    });

    test('off variant has 0 minutes', () {
      expect(SyncInterval.off.minutes, 0);
    });

    test('hour1 variant has 60 minutes', () {
      expect(SyncInterval.hour1.minutes, 60);
    });
  });

  // ── SettingsPrefsData ────────────────────────────────────────────────────────

  group('SettingsPrefsData.copyWith', () {
    const base = SettingsPrefsData(
      syncInterval: SyncInterval.min30,
      wipeOnLogout: false,
      notifyChatReply: true,
      notifyTaskDone: true,
      notifyApprovals: true,
    );

    test('copyWith updates only the specified field', () {
      final updated = base.copyWith(syncInterval: SyncInterval.hour1);
      expect(updated.syncInterval, SyncInterval.hour1);
      // Other fields unchanged.
      expect(updated.wipeOnLogout, false);
      expect(updated.notifyChatReply, true);
      expect(updated.notifyTaskDone, true);
      expect(updated.notifyApprovals, true);
    });

    test('copyWith with wipeOnLogout=true leaves notifications unchanged', () {
      final updated = base.copyWith(wipeOnLogout: true);
      expect(updated.wipeOnLogout, true);
      expect(updated.syncInterval, SyncInterval.min30);
      expect(updated.notifyChatReply, true);
    });

    test('copyWith with all notification toggles disabled', () {
      final updated = base.copyWith(
        notifyChatReply: false,
        notifyTaskDone: false,
        notifyApprovals: false,
      );
      expect(updated.notifyChatReply, false);
      expect(updated.notifyTaskDone, false);
      expect(updated.notifyApprovals, false);
      // syncInterval and wipeOnLogout unchanged.
      expect(updated.syncInterval, SyncInterval.min30);
      expect(updated.wipeOnLogout, false);
    });

    test('copyWith with no args returns equivalent object', () {
      final copy = base.copyWith();
      expect(copy.syncInterval, base.syncInterval);
      expect(copy.wipeOnLogout, base.wipeOnLogout);
      expect(copy.notifyChatReply, base.notifyChatReply);
      expect(copy.notifyTaskDone, base.notifyTaskDone);
      expect(copy.notifyApprovals, base.notifyApprovals);
    });

    test('defaults reminderLeadDefault to the built-in default', () {
      expect(base.reminderLeadDefault, kDefaultReminderLead);
    });

    test('copyWith updates reminderLeadDefault in isolation', () {
      final updated = base.copyWith(reminderLeadDefault: ReminderLead.hour1);
      expect(updated.reminderLeadDefault, ReminderLead.hour1);
      expect(updated.syncInterval, base.syncInterval);
    });
  });

  // ── Reminder-lead-default serialisation ─────────────────────────────────────

  group('reminderLeadToStored / reminderLeadFromStored', () {
    test('round-trips each preset', () {
      for (final lead in const [
        ReminderLead.none,
        ReminderLead.atTime,
        ReminderLead.min10,
        ReminderLead.min30,
        ReminderLead.hour1,
        ReminderLead.day1,
      ]) {
        expect(reminderLeadFromStored(reminderLeadToStored(lead)), lead);
      }
    });

    test('None serialises to the "none" sentinel', () {
      expect(reminderLeadToStored(ReminderLead.none), 'none');
      expect(reminderLeadToStored(ReminderLead.atTime), '0');
      expect(reminderLeadToStored(ReminderLead.hour1), '60');
    });

    test('absent / garbage / negative falls back to the default', () {
      expect(reminderLeadFromStored(null), kDefaultReminderLead);
      expect(reminderLeadFromStored('xyz'), kDefaultReminderLead);
      expect(reminderLeadFromStored('-5'), kDefaultReminderLead);
    });
  });
}
