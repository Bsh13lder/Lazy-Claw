/// On-device recurrence model + standard 5-field cron mapping.
///
/// A task's recurrence is stored on the server as a **standard cron
/// expression** string (`minute hour day-of-month month day-of-week`), e.g.
/// `0 17 * * 1` = "every Monday at 17:00". The backend's `get_next_run(cron)`
/// (croniter) auto-creates the next occurrence when a recurring task is
/// completed, so the mobile app only has to author / parse / display the cron —
/// it never schedules respawns itself.
///
/// This library is pure Dart (no I/O, no Flutter, no async) and never throws —
/// an unrecognized cron simply parses back to a [RecurrenceKind.custom] chip.
///
/// ### Cron field reference
///   * minute       0-59
///   * hour         0-23
///   * day-of-month 1-31  (`*` = any)
///   * month        1-12  (`*` = any)
///   * day-of-week  0-6   where **Sun=0, Mon=1 … Sat=6** (`*` = any)
///
/// Dart's [DateTime.weekday] uses Mon=1 … Sun=7, so Sunday (7) maps to cron 0.
library;

/// The supported recurrence shapes. [none] = does not repeat; [custom] = a cron
/// we didn't author (parsed from an existing task) and can only display, not
/// edit through the picker.
enum RecurrenceKind { none, daily, weekdays, weekly, monthly, yearly, custom }

/// An immutable recurrence selection.
///
/// [weekday] is only meaningful for [RecurrenceKind.weekly]: it pins the cron's
/// day-of-week (Dart convention Mon=1 … Sun=7). When null on a weekly
/// recurrence, the caller's `dueAnchor` weekday is used instead.
class Recurrence {
  final RecurrenceKind kind;

  /// Explicit weekday for [RecurrenceKind.weekly] (Mon=1 … Sun=7), or null to
  /// derive it from the due anchor.
  final int? weekday;

  const Recurrence(this.kind, {this.weekday});

  static const none = Recurrence(RecurrenceKind.none);
  static const daily = Recurrence(RecurrenceKind.daily);
  static const weekdays = Recurrence(RecurrenceKind.weekdays);
  static const weekly = Recurrence(RecurrenceKind.weekly);
  static const monthly = Recurrence(RecurrenceKind.monthly);
  static const yearly = Recurrence(RecurrenceKind.yearly);
  static const custom = Recurrence(RecurrenceKind.custom);

  bool get repeats => kind != RecurrenceKind.none;

  Recurrence copyWith({RecurrenceKind? kind, int? weekday}) =>
      Recurrence(kind ?? this.kind, weekday: weekday ?? this.weekday);

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is Recurrence && other.kind == kind && other.weekday == weekday;

  @override
  int get hashCode => Object.hash(kind, weekday);

  @override
  String toString() => 'Recurrence(${kind.name}, weekday: $weekday)';
}

// ── Cron <-> weekday helpers ────────────────────────────────────────────────

/// Map a Dart weekday (Mon=1 … Sun=7) to the cron day-of-week (Sun=0 … Sat=6).
int _dartWeekdayToCron(int dartWeekday) => dartWeekday % 7;

/// Map a cron day-of-week (0-6, Sun=0) back to a Dart weekday (Mon=1 … Sun=7).
int _cronDowToDart(int cronDow) => cronDow == 0 ? DateTime.sunday : cronDow;

const Map<int, String> _weekdayAbbr = {
  DateTime.monday: 'Mon',
  DateTime.tuesday: 'Tue',
  DateTime.wednesday: 'Wed',
  DateTime.thursday: 'Thu',
  DateTime.friday: 'Fri',
  DateTime.saturday: 'Sat',
  DateTime.sunday: 'Sun',
};

// ── Recurrence -> cron ──────────────────────────────────────────────────────

/// Convert a [Recurrence] into a standard 5-field cron string, or null for
/// [RecurrenceKind.none] / [RecurrenceKind.custom].
///
/// The minute/hour come from [dueAnchor]'s time-of-day when it has one (a full
/// ISO datetime carries a clock); otherwise [defaultHour]:[defaultMinute]
/// (09:00 by default). The day-of-month / month / weekday come from
/// [dueAnchor]'s calendar position where the recurrence needs them:
///   * daily    → `M H * * *`
///   * weekdays → `M H * * 1-5`
///   * weekly   → `M H * * <dow>` (explicit [Recurrence.weekday] wins, else the
///                anchor's weekday, else Monday when no anchor is supplied)
///   * monthly  → `M H <day> * *` (anchor day-of-month, else 1)
///   * yearly   → `M H <day> <month> *` (anchor day + month, else Jan 1)
String? recurrenceToCron(
  Recurrence r, {
  DateTime? dueAnchor,
  int defaultHour = 9,
  int defaultMinute = 0,
}) {
  if (r.kind == RecurrenceKind.none || r.kind == RecurrenceKind.custom) {
    return null;
  }

  final hour = dueAnchor?.hour ?? defaultHour;
  final minute = dueAnchor?.minute ?? defaultMinute;

  switch (r.kind) {
    case RecurrenceKind.daily:
      return '$minute $hour * * *';
    case RecurrenceKind.weekdays:
      return '$minute $hour * * 1-5';
    case RecurrenceKind.weekly:
      final dartWeekday = r.weekday ?? dueAnchor?.weekday ?? DateTime.monday;
      final dow = _dartWeekdayToCron(dartWeekday);
      return '$minute $hour * * $dow';
    case RecurrenceKind.monthly:
      final day = dueAnchor?.day ?? 1;
      return '$minute $hour $day * *';
    case RecurrenceKind.yearly:
      final day = dueAnchor?.day ?? 1;
      final month = dueAnchor?.month ?? 1;
      return '$minute $hour $day $month *';
    case RecurrenceKind.none:
    case RecurrenceKind.custom:
      return null; // unreachable (guarded above) — keeps the switch total.
  }
}

// ── cron -> Recurrence ──────────────────────────────────────────────────────

/// Best-effort parse a cron string back into a [Recurrence] for display /
/// editing of an existing task. Recognizes exactly the shapes
/// [recurrenceToCron] emits; anything else (multi-value lists, step ranges,
/// unexpected field combinations) maps to [RecurrenceKind.custom] so it renders
/// as a non-editable "Repeats" chip rather than crashing.
///
/// A null / blank cron → [RecurrenceKind.none].
Recurrence recurrenceFromCron(String? cron) {
  if (cron == null) return Recurrence.none;
  final trimmed = cron.trim();
  if (trimmed.isEmpty) return Recurrence.none;

  final fields = trimmed.split(RegExp(r'\s+'));
  if (fields.length != 5) return Recurrence.custom;

  final minute = fields[0];
  final hour = fields[1];
  final dom = fields[2]; // day-of-month
  final month = fields[3];
  final dow = fields[4]; // day-of-week

  // The clock fields must be plain integers for any shape we author.
  if (_asInt(minute) == null || _asInt(hour) == null) {
    return Recurrence.custom;
  }

  // daily — `M H * * *`
  if (dom == '*' && month == '*' && dow == '*') {
    return Recurrence.daily;
  }

  // weekdays — `M H * * 1-5`
  if (dom == '*' && month == '*' && dow == '1-5') {
    return Recurrence.weekdays;
  }

  // weekly — `M H * * <0-6>`
  if (dom == '*' && month == '*') {
    final cronDow = _asInt(dow);
    if (cronDow != null && cronDow >= 0 && cronDow <= 6) {
      return Recurrence(
        RecurrenceKind.weekly,
        weekday: _cronDowToDart(cronDow),
      );
    }
    return Recurrence.custom;
  }

  // monthly — `M H <day> * *`
  if (month == '*' && dow == '*') {
    final day = _asInt(dom);
    if (day != null && day >= 1 && day <= 31) return Recurrence.monthly;
    return Recurrence.custom;
  }

  // yearly — `M H <day> <month> *`
  if (dow == '*') {
    final day = _asInt(dom);
    final mon = _asInt(month);
    if (day != null &&
        day >= 1 &&
        day <= 31 &&
        mon != null &&
        mon >= 1 &&
        mon <= 12) {
      return Recurrence.yearly;
    }
    return Recurrence.custom;
  }

  return Recurrence.custom;
}

/// Parse a single cron field as a plain non-negative integer, or null when it
/// carries any cron syntax (`*`, `,`, `-`, `/`) or isn't a number.
int? _asInt(String field) {
  if (field.isEmpty) return null;
  return int.tryParse(field);
}

// ── Display label ────────────────────────────────────────────────────────────

/// A short human label for a [Recurrence], for the recurrence chip / picker:
/// "Daily" · "Weekdays" · "Weekly (Mon)" · "Monthly" · "Yearly" · "Repeats"
/// (custom) · "Does not repeat" (none).
String recurrenceLabel(Recurrence r) {
  switch (r.kind) {
    case RecurrenceKind.none:
      return 'Does not repeat';
    case RecurrenceKind.daily:
      return 'Daily';
    case RecurrenceKind.weekdays:
      return 'Weekdays';
    case RecurrenceKind.weekly:
      final wd = r.weekday;
      final abbr = wd == null ? null : _weekdayAbbr[wd];
      return abbr == null ? 'Weekly' : 'Weekly ($abbr)';
    case RecurrenceKind.monthly:
      return 'Monthly';
    case RecurrenceKind.yearly:
      return 'Yearly';
    case RecurrenceKind.custom:
      return 'Repeats';
  }
}

/// The short chip label for a STORED cron value (convenience for task rows):
/// parses the cron then labels it. Null/blank cron → null (no chip).
String? cronChipLabel(String? cron) {
  final r = recurrenceFromCron(cron);
  if (r.kind == RecurrenceKind.none) return null;
  return recurrenceLabel(r);
}

/// Build the [recurrenceToCron] anchor from a `dueDate` string (the dual-shape
/// `yyyy-MM-dd` or `yyyy-MM-ddTHH:mm:ss` value used across the Tasks UI).
///
/// A **timed** due contributes its full clock + calendar position. A
/// **date-only** due contributes only its calendar position — its (midnight)
/// clock is replaced with [defaultHour]:[defaultMinute] (09:00) so a date-only
/// recurring task doesn't silently land at 00:00. A null/blank/unparseable due
/// returns null, letting the cron fall back to the defaults entirely.
DateTime? recurrenceAnchorFromDue(
  String? due, {
  int defaultHour = 9,
  int defaultMinute = 0,
}) {
  if (due == null || due.isEmpty) return null;
  final parsed = DateTime.tryParse(due);
  if (parsed == null) return null;
  // A `T`-bearing ISO datetime carries a real clock — keep it as-is.
  if (due.length > 10 && due.contains('T')) return parsed;
  // Date-only → keep the calendar date, default the clock.
  return DateTime(
    parsed.year,
    parsed.month,
    parsed.day,
    defaultHour,
    defaultMinute,
  );
}
