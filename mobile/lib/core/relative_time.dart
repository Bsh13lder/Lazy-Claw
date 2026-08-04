/// The app's ONE timestamp label: relative while a moment is recent, an
/// absolute calendar date once it isn't.
///
/// ## Why this exists here
/// Half a dozen screens grew their own private `_relativeTime` (comments,
/// activity, audit, watchers, documents, notifications). That was fine while
/// each one only had to label its own feed, but tasks, sub-tasks and comments
/// now render the SAME kind of value side by side inside one sheet — three
/// spellings of "when" in one screen reads as three different things. This is
/// the promoted version of the comments-section helper, which was the only one
/// that already had the property that matters: it stops counting days and
/// switches to a real date, so a task created eight months ago reads
/// `Dec 4, 2025` rather than `241d ago`.
///
/// Pure Dart (no Flutter, no intl) so it is trivially unit-testable and safe
/// to call from anywhere — same discipline as `core/due_date.dart`.
library;

/// Month abbreviations, indexed 1-12 (slot 0 unused so `[dt.month]` reads
/// directly). Matches the vocabulary `friendlyDate` already uses on the
/// Expenses screen, so a date means the same thing in both places.
const List<String> kShortMonths = [
  '',
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

/// Everything under this age reads relatively; anything older reads as a date.
///
/// A week is where "N days ago" stops being something a human can convert
/// without counting on their fingers.
const Duration kRelativeTimeCutoff = Duration(days: 7);

/// A human label for the ISO-8601 timestamp [iso], or **null** when there is
/// nothing to say.
///
/// Null is the load-bearing return: callers render NOTHING for it. Sub-tasks
/// created before `created_at` existed have no value and never will, and a
/// placeholder ("—", "unknown", a 1970 date) would read as a bug rather than
/// as absence.
///
/// Tolerant by the same rule as `Subtask._coerceTimestamp`: null, empty,
/// whitespace and unparseable input all degrade to null instead of throwing —
/// a hand-edited or half-migrated blob must cost one missing date, never the
/// whole row.
///
/// Timestamps are stored UTC and rendered in the device's LOCAL zone. The
/// comparison against [now] is between INSTANTS (Dart's `difference` works off
/// epoch microseconds regardless of either side's `isUtc` flag), so an offset
/// spelling — the server's `+00:00` vs the client's `Z` — can never shift the
/// answer. `.toLocal()` is applied before reading calendar fields, matching
/// the convention `groupTasksByDay` documents: this project has repeatedly
/// shipped naive-vs-UTC drift, and reading `.day` off the raw parse is exactly
/// how.
///
/// [now] is injectable so tests pin the clock instead of racing it.
String? formatTimestampLabel(String? iso, {DateTime? now}) {
  if (iso == null) return null;
  final trimmed = iso.trim();
  if (trimmed.isEmpty) return null;
  final parsed = DateTime.tryParse(trimmed);
  if (parsed == null) return null;

  final clock = now ?? DateTime.now();
  final diff = clock.difference(parsed);

  // A future timestamp is clock skew, not a forward appointment: both the
  // client and the server stamp these, seconds apart, off unsynchronised
  // clocks. "in 3m" on a row the user just ticked would read as a bug.
  if (diff.isNegative || diff.inSeconds < 60) return 'just now';
  if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
  if (diff.inHours < 24) return '${diff.inHours}h ago';
  if (diff < kRelativeTimeCutoff) return '${diff.inDays}d ago';

  return _absoluteDate(parsed.toLocal(), clock.toLocal());
}

/// `Jun 6` for the current year, `Dec 4, 2025` for any other.
///
/// The year is dropped on the common case because it is noise — and kept
/// otherwise because "Dec 4" on a task filed two Decembers ago is a lie of
/// omission.
String _absoluteDate(DateTime local, DateTime nowLocal) {
  final month = kShortMonths[local.month];
  return local.year == nowLocal.year
      ? '$month ${local.day}'
      : '$month ${local.day}, ${local.year}';
}
