/// The task detail sheet's due-date / reminder derivations, as one immutable
/// value object.
///
/// Extracted out of `task_detail_sheet.dart` VERBATIM — same rules, same
/// comments. WHY it earns its own file: these four derivations are the most
/// bug-prone logic in the sheet (see [reminderArg]'s doc for the incident that
/// permanently deleted reminders on an unrelated edit), and as `State` getters
/// they could only ever be exercised by driving a whole widget tree. Here each
/// rule is a pure function of seven explicit inputs.
///
/// Immutable by construction: the sheet rebuilds one of these from its current
/// fields whenever it needs a derivation, rather than caching and invalidating.
library;

import 'package:flutter/material.dart' show TimeOfDay;
import 'package:lazyclaw_mobile/core/due_date.dart';
import 'package:lazyclaw_mobile/core/reminder_lead.dart';

class TaskDueModel {
  const TaskDueModel({
    required this.dueDay,
    required this.dueTime,
    required this.originalReminderAt,
    required this.explicitLead,
    required this.defaultLead,
    required this.reminderTouched,
    required this.dueTouched,
    this.now,
  });

  /// The selected calendar day (`yyyy-MM-dd`), or null for none.
  final String? dueDay;
  final TimeOfDay? dueTime;

  /// The task's `reminderAt` on open (null / '' = none).
  final String? originalReminderAt;

  /// The user's explicit lead choice, or null to fall back to [defaultLead].
  final ReminderLead? explicitLead;
  final ReminderLead defaultLead;

  /// Whether the user touched the REMIND control (a lead chip, or the ✕).
  final bool reminderTouched;

  /// Whether the user changed the due day or time.
  final bool dueTouched;

  /// Injectable clock — only used for the time-without-a-day case, where
  /// "today" has to come from somewhere. Defaults to [DateTime.now].
  final DateTime? now;

  /// The effective reminder lead (explicit choice wins over the global
  /// default).
  ReminderLead get effectiveLead => explicitLead ?? defaultLead;

  /// Combine the day + time into the persisted `dueDate` string: a datetime
  /// when a time is set, a date-only string when only a day is set, today+time
  /// when only a time is set, else null.
  String? get composedDue {
    final day = dueDay;
    final time = dueTime;
    if (day == null) {
      if (time == null) return null;
      final n = now ?? DateTime.now();
      return composeDueDate(
        DateTime(n.year, n.month, n.day),
        hour: time.hour,
        minute: time.minute,
      );
    }
    final d = DateTime.tryParse(day);
    if (d == null) return day;
    return composeDueDate(
      DateTime(d.year, d.month, d.day),
      hour: time?.hour,
      minute: time?.minute,
    );
  }

  /// The reminderAt string to persist: `''` (clear) when there's no due time
  /// or the lead is None, else the absolute `due − lead` instant.
  String get composedReminderAt => resolveReminderAt(
    dueDate: composedDue,
    explicitLead: explicitLead,
    defaultLead: defaultLead,
  );

  /// The reminder instant that SURVIVES this edit when the due date has no
  /// time-of-day, or null when nothing survives. This is the only path that
  /// can see a date-only task's reminder, since `due − lead` degenerates for
  /// it.
  ///
  /// * user touched the REMIND control → null (an explicit clear)
  /// * no reminder to begin with → null
  /// * due date removed entirely → null (a reminder with nothing to remind
  ///   about is an orphan that would still nag)
  /// * due day untouched → the stored instant, verbatim
  /// * due day moved → the SAME clock time re-anchored onto the new day (the
  ///   Smart-Reschedule shape, cf. [cardDueTimeLabel]) rather than a reminder
  ///   stranded on the old date
  String? get survivingReminderAt {
    if (reminderTouched) return null;
    final original = originalReminderAt;
    if (original == null || original.isEmpty) return null;
    final due = composedDue;
    if (due == null) return null;
    if (!dueTouched) return original;
    final parts = dueTimeParts(original);
    final day = DateTime.tryParse(due);
    if (parts == null || day == null) return original;
    return composeDueDate(day, hour: parts.hour, minute: parts.minute);
  }

  /// The `reminderAt` argument for `TasksNotifier.updateTask`: `null` leaves
  /// the column untouched, `''` force-clears it, anything else sets it.
  ///
  /// WHY this is not simply [composedReminderAt]: `resolveReminderAt` can only
  /// answer `''` for a due date with no time-of-day, and EVERY
  /// backend-respawned recurring occurrence (plus every Smart-Rescheduled
  /// task) has exactly that shape — a date-only due plus a real timed
  /// `reminder_at`. Sending the composed value unconditionally meant opening a
  /// repeating task to fix a typo and hitting Save permanently deleted its
  /// reminder, its server reminder job and its advance nags.
  String? get reminderArg {
    // A timed due makes the reminder fully derivable, and the lead picker IS
    // the user-visible control for it — so it stays authoritative (status quo).
    if (dueDateHasTime(composedDue)) return composedReminderAt;
    final surviving = survivingReminderAt;
    if (surviving == null) return '';
    // Unchanged → leave the column absent from the patch, matching how this
    // sheet already avoids churning category / recurring / tags / steps.
    if (surviving == originalReminderAt) return null;
    return surviving;
  }
}
