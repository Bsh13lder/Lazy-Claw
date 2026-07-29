/// Reminder-OFFSET model + pure math for scheduling *multiple* local reminders
/// per task ("unlimited reminders", Proton-Calendar style).
///
/// The user picks a SET of default offsets in Settings; every task with a due
/// time then schedules one local notification per offset. Offsets are persisted
/// to the server (`users.settings.general.reminder_offsets`) so Telegram-side
/// reminders (a parallel backend workstream) fire the same set.
///
/// ## Wire format
/// A signed `<n><unit>` string, units `d`/`h`/`m`, where a NEGATIVE magnitude
/// means "fire that long BEFORE the due time":
///   * `0m`      → at the due time
///   * `-10m`    → 10 minutes before
///   * `-30m`    → 30 minutes before
///   * `-1h`     → 1 hour before
///   * `-1d`     → 1 day before
///   * `-2h30m`  → 2h30m before (compound)
///
/// Fire instant = `base + parseReminderOffset(offset)` (the offset Duration is
/// already signed, so a negative offset subtracts from `base`).
///
/// Pure Dart — every function here is trivially unit-testable with no Flutter
/// binding, no plugin, and an injectable `now`.
library;

/// A user-facing default-reminder offset option shown in Settings.
class ReminderOffsetOption {
  /// The wire value persisted to the server (e.g. `'0m'`, `'-30m'`).
  final String value;

  /// The human label shown on the chip (e.g. `'At time'`, `'30 min before'`).
  final String label;

  const ReminderOffsetOption(this.value, this.label);
}

/// The offsets offered in Settings → Notifications, in display order.
const List<ReminderOffsetOption> kReminderOffsetOptions = <ReminderOffsetOption>[
  ReminderOffsetOption('0m', 'At time'),
  ReminderOffsetOption('-10m', '10 min before'),
  ReminderOffsetOption('-30m', '30 min before'),
  ReminderOffsetOption('-1h', '1 hour before'),
  ReminderOffsetOption('-2h', '2 hours before'),
  ReminderOffsetOption('-1d', '1 day before'),
];

/// Default offset set when nothing is stored (the backend hasn't sent
/// `reminder_offsets` yet, or prefs are absent/unparseable). MUST match the
/// server default — `lazyclaw/settings/general.py` DEFAULT_GENERAL
/// `"reminder_offsets": ["0m", "-2h", "-1h"]` — so an un-customised task fires the
/// same advance reminders on the phone as the backend/Telegram side. Was
/// `['-30m']`, which silently diverged from the server (T5).
const List<String> kDefaultReminderOffsets = <String>['0m', '-2h', '-1h'];

/// The canonical "fire AT the event" offset.
///
/// [TaskReminderService] ALWAYS schedules this one in addition to whatever the
/// user selected: the default set above is advance-only, so out of the box a task
/// due 08:00 buzzed at 06:00 and 07:00 and then went SILENT at 08:00 — the only
/// at-time nudge was the server's Telegram nag, which app-only users never
/// receive. It is added at SCHEDULE time (not migrated into the stored pref) so
/// the user's Settings selection is never rewritten behind their back.
const String kAtTimeReminderOffset = '0m';

final RegExp _kOffsetGroup = RegExp(r'(\d+)([dhm])');

/// Parse a wire offset string into a SIGNED [Duration], or `null` when the
/// string is malformed. `0m` → [Duration.zero]. A leading `-` negates the whole
/// magnitude (`-2h30m` → −150 min). Sign-faithful: an unsigned magnitude parses
/// as a positive duration (normalisation to negative happens at storage time).
Duration? parseReminderOffset(String raw) {
  final s = raw.trim();
  if (s.isEmpty) return null;
  var body = s;
  var negative = false;
  if (body.startsWith('-')) {
    negative = true;
    body = body.substring(1);
  } else if (body.startsWith('+')) {
    body = body.substring(1);
  }
  if (body.isEmpty) return null;

  final matches = _kOffsetGroup.allMatches(body).toList();
  if (matches.isEmpty) return null;
  // Reject stray characters: the body must be EXACTLY a run of <n><unit> groups
  // (so `1h30`, `30x`, `1.5h` are rejected — they can't fully rebuild).
  if (matches.map((m) => m.group(0)).join() != body) return null;

  var totalMinutes = 0;
  for (final m in matches) {
    final value = int.parse(m.group(1)!);
    switch (m.group(2)) {
      case 'd':
        totalMinutes += value * 60 * 24;
      case 'h':
        totalMinutes += value * 60;
      case 'm':
        totalMinutes += value;
    }
  }
  return Duration(minutes: negative ? -totalMinutes : totalMinutes);
}

/// Serialise a signed [Duration] back to its canonical wire string. Zero → `0m`;
/// otherwise the sign prefix followed by the largest-first `<n>d<n>h<n>m` groups
/// (only non-zero groups appear): −150 min → `-2h30m`, −1440 min → `-1d`.
String serializeReminderOffset(Duration d) {
  final total = d.inMinutes;
  if (total == 0) return '0m';
  final negative = total < 0;
  var mins = total.abs();
  final days = mins ~/ (60 * 24);
  mins %= 60 * 24;
  final hours = mins ~/ 60;
  mins %= 60;

  final sb = StringBuffer();
  if (negative) sb.write('-');
  if (days > 0) sb.write('${days}d');
  if (hours > 0) sb.write('${hours}h');
  if (mins > 0) sb.write('${mins}m');
  return sb.toString();
}

/// Canonicalise a raw offset string to its serialized form (so `-60m` → `-1h`,
/// `-0m` → `0m`), or `null` when the string is malformed.
String? canonicalReminderOffset(String raw) {
  final d = parseReminderOffset(raw);
  return d == null ? null : serializeReminderOffset(d);
}

/// Sanitise a stored/loaded offsets list for persistence + display: drop
/// malformed entries, canonicalise, dedupe, and sort earliest-fire-first (most
/// negative offset first — `-1d`, `-1h`, `-30m`, `0m`).
List<String> normalizeReminderOffsets(Iterable<String> raw) {
  final seen = <String>{};
  final parsed = <({String wire, int minutes})>[];
  for (final r in raw) {
    final d = parseReminderOffset(r);
    if (d == null) continue;
    final wire = serializeReminderOffset(d);
    if (seen.add(wire)) parsed.add((wire: wire, minutes: d.inMinutes));
  }
  parsed.sort((a, b) => a.minutes.compareTo(b.minutes));
  return [for (final p in parsed) p.wire];
}

/// Compute the sorted, de-duplicated list of ABSOLUTE fire instants for [base]
/// given the configured [offsets].
///
/// Each fire = `base + parseReminderOffset(offset)`. Malformed offsets are
/// ignored. Fires that resolve to more than [grace] in the past (relative to
/// [now]) are skipped so we never schedule an already-elapsed reminder. When
/// [offsets] is empty the result is the single [base] instant (subject to the
/// same past-filter) — preserving single-reminder behaviour.
List<DateTime> computeOffsetFireTimes({
  required DateTime base,
  required List<String> offsets,
  DateTime? now,
  Duration grace = const Duration(minutes: 1),
}) {
  final cutoff = (now ?? DateTime.now()).subtract(grace);

  bool keep(DateTime t) => !t.isBefore(cutoff);

  if (offsets.isEmpty) {
    return keep(base) ? <DateTime>[base] : const <DateTime>[];
  }

  final seen = <DateTime>{};
  final fires = <DateTime>[];
  for (final raw in offsets) {
    final off = parseReminderOffset(raw);
    if (off == null) continue;
    final fire = base.add(off);
    if (!keep(fire)) continue;
    if (seen.add(fire)) fires.add(fire);
  }
  fires.sort();
  return fires;
}
