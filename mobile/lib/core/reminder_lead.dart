/// Reminder lead-time model + pure math for scheduling task reminders *ahead*
/// of their due time.
///
/// The app stores a reminder as an **absolute** `Task.reminderAt` ISO string so
/// the existing scheduler (`reminderFireTime` → `zonedSchedule`) keeps working
/// unchanged — it already fires at `reminderAt`. A [ReminderLead] is just the
/// user-facing *relative* expression of that instant: `reminderAt = due − lead`.
/// The lead is derived back for display via [leadFromReminderAt].
///
/// Pure Dart (only depends on [due_date]) so every function here is trivially
/// unit-testable with no Flutter binding.
library;

import 'due_date.dart';

/// A reminder lead-time, expressed relative to a task's due datetime.
class ReminderLead {
  /// How far BEFORE the due time the reminder should fire.
  ///   * `null`          → "None": no explicit reminder (clears `reminderAt`).
  ///   * `Duration.zero` → "At time": fire exactly at the due time.
  ///   * positive        → fire that long before the due time.
  final Duration? lead;

  const ReminderLead(this.lead);

  static const none = ReminderLead(null);
  static const atTime = ReminderLead(Duration.zero);
  static const min10 = ReminderLead(Duration(minutes: 10));
  static const min30 = ReminderLead(Duration(minutes: 30));
  static const hour1 = ReminderLead(Duration(hours: 1));
  static const day1 = ReminderLead(Duration(days: 1));

  /// The preset chips shown in the picker, in display order.
  static const presets = <ReminderLead>[
    none,
    atTime,
    min10,
    min30,
    hour1,
    day1,
  ];

  /// "None" — no reminder should be scheduled.
  bool get isNone => lead == null;

  /// True when this is a non-preset positive duration. The picker reveals the
  /// number + unit custom inputs for these (a custom value that happens to equal
  /// a preset, e.g. 10 minutes, is treated as that preset via value equality).
  bool get isCustom => lead != null && !presets.contains(this);

  /// Human label, e.g. `None`, `At time`, `10 min before`, `1 hour before`,
  /// `2 days before`, `45 min before`.
  String get label {
    final d = lead;
    if (d == null) return 'None';
    if (d == Duration.zero) return 'At time';
    if (d.inMinutes % (60 * 24) == 0) {
      final n = d.inDays;
      return '$n day${n == 1 ? '' : 's'} before';
    }
    if (d.inMinutes % 60 == 0) {
      final n = d.inHours;
      return '$n hour${n == 1 ? '' : 's'} before';
    }
    final n = d.inMinutes;
    return '$n min before';
  }

  @override
  bool operator ==(Object other) =>
      other is ReminderLead && other.lead == lead;

  @override
  int get hashCode => lead.hashCode;

  @override
  String toString() => 'ReminderLead(${lead ?? 'none'})';
}

/// Unit for the "Custom…" lead input.
enum ReminderLeadUnit {
  minutes('Min'),
  hours('Hr'),
  days('Day');

  const ReminderLeadUnit(this.label);
  final String label;

  Duration toDuration(int value) => switch (this) {
        ReminderLeadUnit.minutes => Duration(minutes: value),
        ReminderLeadUnit.hours => Duration(hours: value),
        ReminderLeadUnit.days => Duration(days: value),
      };
}

/// Decompose a custom lead [Duration] into the most natural `(value, unit)` for
/// the custom inputs: whole days → days, whole hours → hours, else minutes.
({int value, ReminderLeadUnit unit}) decomposeCustomLead(Duration d) {
  if (d.inDays >= 1 && d.inMinutes % (60 * 24) == 0) {
    return (value: d.inDays, unit: ReminderLeadUnit.days);
  }
  if (d.inHours >= 1 && d.inMinutes % 60 == 0) {
    return (value: d.inHours, unit: ReminderLeadUnit.hours);
  }
  return (value: d.inMinutes, unit: ReminderLeadUnit.minutes);
}

// ── Pure math ────────────────────────────────────────────────────────────────

/// The absolute instant a reminder should fire for a task with [dueDate] and the
/// chosen [lead], or `null` when nothing should be scheduled:
///   * [lead] is `None`,
///   * [dueDate] has no time-of-day (a bare `yyyy-MM-dd` — we never invent a
///     time), or
///   * [dueDate] is unparseable.
///
/// `At time` → the due instant itself; an N-before lead → `due − N`. (A lead
/// that lands in the past is NOT filtered here — that's `reminderFireTime`'s job
/// at schedule time, so the picker can still show a chosen lead.)
DateTime? computeReminderAt({
  required String? dueDate,
  required ReminderLead lead,
}) {
  if (lead.isNone) return null;
  if (!dueDateHasTime(dueDate)) return null;
  final due = DateTime.tryParse(dueDate!);
  if (due == null) return null;
  return due.subtract(lead.lead!);
}

/// Derive the [ReminderLead] implied by an absolute [reminderAt] relative to a
/// timed [dueDate], for display in the picker. Returns [ReminderLead.none] when
/// there's no reminder, the due has no time-of-day, or either value is
/// unparseable. A reminder at/after the due → `At time`. Canonicalises to a
/// preset when the gap matches one, else a custom lead.
ReminderLead leadFromReminderAt(String? dueDate, String? reminderAt) {
  if (reminderAt == null || reminderAt.isEmpty) return ReminderLead.none;
  if (!dueDateHasTime(dueDate)) return ReminderLead.none;
  final due = DateTime.tryParse(dueDate!);
  final rem = DateTime.tryParse(reminderAt);
  if (due == null || rem == null) return ReminderLead.none;
  var diff = due.difference(rem);
  if (diff.isNegative) diff = Duration.zero;
  return ReminderLead(diff);
}

/// Format a reminder instant as the local ISO string the store expects — same
/// `yyyy-MM-ddTHH:mm:00` shape as [composeDueDate] (no millis, no `Z`), so it
/// round-trips through `DateTime.parse` / `tz.TZDateTime.from` like a due date.
String formatReminderIso(DateTime dt) => composeDueDate(
      DateTime(dt.year, dt.month, dt.day),
      hour: dt.hour,
      minute: dt.minute,
    );

/// Resolve the `reminderAt` string to PERSIST for a task, given the final
/// [dueDate], the user's [explicitLead] picker choice (`null` = untouched, so
/// the global [defaultLead] applies), and that [defaultLead].
///
/// Returns:
///   * `''` (clear) when the due has no time-of-day, or the effective lead is
///     `None` — callers pass `''` to an update to force-clear the column.
///   * the absolute ISO instant (`due − lead`) otherwise.
///
/// Never returns null, so an update always either sets or clears the field
/// deterministically (a create may normalise `''` → no reminder).
String resolveReminderAt({
  required String? dueDate,
  required ReminderLead? explicitLead,
  required ReminderLead defaultLead,
}) {
  if (!dueDateHasTime(dueDate)) return '';
  final lead = explicitLead ?? defaultLead;
  final at = computeReminderAt(dueDate: dueDate, lead: lead);
  if (at == null) return '';
  return formatReminderIso(at);
}
