import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../core/due_date.dart';
import '../../core/recurrence.dart';
import '../../models/project.dart';
import '../../models/task.dart';
import 'task_sort.dart';

/// Pure, framework-light helpers backing the Tasks calendar view.
///
/// Kept free of any widget / TableCalendar coupling so the coloring + grouping
/// logic is trivially unit-testable.

/// Buckets [tasks] by their due **day** (time component ignored).
///
/// Tasks with no `dueDate`, or whose `dueDate` can't be parsed, are dropped —
/// the calendar only plots dated work. Keys are date-only `DateTime`s at local
/// midnight (`DateTime(y, m, d)`), so they collide for same-day tasks regardless
/// of any time-of-day in the stored ISO string.
Map<DateTime, List<Task>> groupTasksByDay(List<Task> tasks) {
  final out = <DateTime, List<Task>>{};
  for (final task in tasks) {
    final raw = task.dueDate;
    if (raw == null || raw.isEmpty) continue;
    final DateTime parsed;
    try {
      parsed = DateTime.parse(raw);
    } catch (_) {
      // Non-ISO / malformed → not plottable. Log instead of a silent
      // `continue` so a bad due_date is diagnosable instead of the task
      // just vanishing from the calendar with no trace (D2).
      debugPrint(
        'groupTasksByDay: unparseable dueDate "$raw" on task ${task.id} — '
        'skipped',
      );
      continue;
    }
    // A tz-aware due date (the server emits this for a recurring template
    // whose anchor was tz-aware — see tasks/store.py) parses to a UTC
    // instant; reading .year/.month/.day off it directly buckets by the
    // UTC day, which is off-by-one for any positive UTC offset (e.g.
    // Europe/Madrid). `.toLocal()` resolves it to the wall-clock day the
    // user actually set — matches `due_date.dart:31`'s convention. A no-op
    // on an already-naive/local value.
    final local = parsed.toLocal();
    final key = DateTime(local.year, local.month, local.day);
    (out[key] ??= <Task>[]).add(task);
  }
  // Sort each day's bucket: pending tasks first, done tasks sink to bottom.
  return out.map((day, tasks) => MapEntry(day, sortDoneLast(tasks)));
}

/// How far past **today** [expandRecurringForRange] is willing to project, in
/// days (the horizon day itself is inclusive, so the window is
/// `today .. today + kGhostHorizonDays`).
///
/// ~3 months. Paging one or two months ahead should still show the repeats
/// that are genuinely coming up — that is the whole point of the ghost
/// projection — but the calendar must NOT paint a speculative dot on every
/// day of a month a year out. A dot that far ahead carries zero information
/// (of course a daily task "repeats" in July 2027) and reads as data the
/// user actually entered, which is exactly how the 2026-08-03 report landed:
/// *"still repeating recurring tasks all year, this is a bug, I did not set
/// it up like that."*
///
/// [expandRecurringForRange] takes this as an optional parameter so tests can
/// pin the bound instead of doing calendar arithmetic against the default.
const int kGhostHorizonDays = 92;

/// Absolute ceiling on the ghost days a single recurring task may occupy.
///
/// This bounds the **window**, not a running counter: the loop below emits at
/// most ONE ghost per calendar day per task, so clamping the projected window
/// to at most this many days makes the ceiling structurally unreachable.
///
/// It used to be an in-loop counter (`generated < kMaxGhostsPerTask`, then
/// 60) racing an unbounded range, which is the failure mode this shape
/// exists to prevent: a 92-day horizon over a daily cron produces 93
/// candidate days, so a 60-cap would have shown repeats for the first two
/// months of the window and then silently stopped — a *partially filled*
/// range, indistinguishable from another bug. With the clamp there is only
/// ever one meaningful bound (the horizon, or the caller's own range, or the
/// task's [Task.recurUntil] — whichever ends first), and whatever comes back
/// is always gap-free.
///
/// Set well above what the default horizon can produce (93) so it only ever
/// engages for a caller that explicitly asks for an absurd `horizonDays`.
const int kMaxGhostsPerTask = 200;

/// Projects each recurring task's upcoming occurrences across
/// [rangeStart]..[rangeEnd] (inclusive, local calendar days) as GHOST
/// entries — display-only stand-ins for the fact that the server
/// materialises exactly ONE occurrence of a recurring task at a time
/// (`tasks/store.py` respawns the next occurrence only on completion) and
/// nothing on the client expands `recurring` for display. Without this, a
/// `0 8 * * *` daily task occupies a single cell of the calendar.
///
/// Only tasks with a non-empty [Task.recurring] cron contribute. The cron is
/// classified via [recurrenceFromCron] (the same helper the recurrence
/// picker/chip use) — [RecurrenceKind.none] and [RecurrenceKind.custom]
/// (unparseable, or a shape [recurrenceFromCron] doesn't recognize, e.g. a
/// `*/15` step value) yield NO ghosts for that task rather than guessing.
/// This is deliberately not a general cron engine — only the shapes the
/// on-device recurrence picker authors are day-stepped: daily, weekdays,
/// weekly-on-weekday, monthly-on-day, yearly.
///
/// The task's own materialised due day (from [Task.dueDate], local-day
/// bucketed the same way [groupTasksByDay] does) is skipped — the real
/// [TaskRow] already renders there, so a ghost would duplicate it.
///
/// A **done** task never projects. On completion the server materialises the
/// next occurrence as a brand-new row carrying the same cron
/// (`tasks/store.py`) while the completed row KEEPS its `recurring` value —
/// both reach the client, so a done occurrence that still projected would
/// double-dot every future match day for what is one single series. (When a
/// respawn *fails* the done row is the only one left, but the server's
/// `status == 'done'` guard means it can never respawn again either, so
/// projecting from it would be advertising repeats that will never come.)
///
/// ### The three bounds
/// A ghost day must clear ALL of these; whichever ends first wins, and the
/// result is always a contiguous run (never a partially-filled range):
///  1. **Never the past** — no ghost before [now]'s local calendar day
///     (defaults to the wall clock; pass it explicitly for deterministic
///     tests). Ghosts are a forward-looking "here's the next repeat" hint,
///     never a history: paging back to a month before the task existed must
///     not paint dots on every matching day of it. A ghost exactly ON today
///     IS allowed — today isn't "the past" yet.
///  2. **Never past the horizon** — no ghost after `today + [horizonDays]`
///     (see [kGhostHorizonDays] for why, and [kMaxGhostsPerTask] for the
///     clamp that keeps this the single meaningful forward bound).
///  3. **Never past the user's own series end** — no ghost after
///     [Task.recurUntil]'s local day, INCLUSIVE of that day (the stored
///     `yyyy-MM-dd` means "the series runs through the end of this day", and
///     the final occurrence is the one the user most wants to see coming).
///     A null / blank / unparseable value means "never ends", never "ends
///     immediately" — failing open here mirrors `tasks/store.py`'s
///     `_series_expired`, which also returns "not expired" on a bad parse.
///     The server already refuses to respawn past this date, so a calendar
///     that ignored it (as this did until 2026-08-03) promised repeats that
///     were never going to be materialised.
Map<DateTime, List<Task>> expandRecurringForRange(
  List<Task> tasks,
  DateTime rangeStart,
  DateTime rangeEnd, {
  DateTime? now,
  int horizonDays = kGhostHorizonDays,
}) {
  final start = DateTime(rangeStart.year, rangeStart.month, rangeStart.day);
  final end = DateTime(rangeEnd.year, rangeEnd.month, rangeEnd.day);
  final out = <DateTime, List<Task>>{};
  if (end.isBefore(start)) return out;

  final today = _localDay(now ?? DateTime.now());
  // Clamped so a caller can neither invert the window (negative) nor widen it
  // past what [kMaxGhostsPerTask] can hold — see that constant for why the
  // ceiling lives on the window instead of on a per-task counter.
  final span = math.max(0, math.min(horizonDays, kMaxGhostsPerTask - 1));
  final horizonEnd = _addDays(today, span);

  final loopStart = start.isBefore(today) ? today : start;
  final loopEnd = end.isAfter(horizonEnd) ? horizonEnd : end;
  if (loopStart.isAfter(loopEnd)) return out;

  for (final task in tasks) {
    for (final day in _ghostDaysForTask(task, loopStart, loopEnd)) {
      (out[day] ??= <Task>[]).add(task);
    }
  }
  return out;
}

/// The local-midnight days [task] should ghost on within [from]..[to]
/// (both inclusive, both already clamped to the caller's range / today /
/// the horizon by [expandRecurringForRange]). Empty for anything that must
/// not project — see that function's doc for every rule enforced here.
List<DateTime> _ghostDaysForTask(Task task, DateTime from, DateTime to) {
  final cron = task.recurring;
  if (cron == null || cron.trim().isEmpty) return const [];
  if (task.isDone) return const [];

  final spec = _ghostSpecFromCron(cron);
  if (spec == null) return const []; // Unparseable / unsupported → no ghosts.

  // The user's explicit "Ends on" date tightens the window for THIS task only.
  final seriesEnd = localDueDay(task.recurUntil);
  final last = (seriesEnd != null && seriesEnd.isBefore(to)) ? seriesEnd : to;
  if (from.isAfter(last)) return const [];

  final realDay = localDueDay(task.dueDate);
  final days = <DateTime>[];
  for (var day = from; !day.isAfter(last); day = _addDays(day, 1)) {
    if (spec.matches(day) && day != realDay) days.add(day);
  }
  return days;
}

/// The day-stepping rule a recognized recurrence cron reduces to — the
/// fields [Recurrence] itself doesn't retain (day-of-month, month) resolved
/// once per task instead of re-parsed inside the day loop. Returns null for
/// any cron the on-device picker didn't author, per "this is deliberately
/// not a general cron engine".
_GhostSpec? _ghostSpecFromCron(String cron) {
  final recurrence = recurrenceFromCron(cron);
  if (!recurrence.repeats || recurrence.kind == RecurrenceKind.custom) {
    return null;
  }
  if (recurrence.kind != RecurrenceKind.monthly &&
      recurrence.kind != RecurrenceKind.yearly) {
    return _GhostSpec(recurrence.kind, weekday: recurrence.weekday);
  }

  final fields = _cronFields(cron);
  if (fields == null) return null;
  final dayOfMonth = int.tryParse(fields[2]);
  if (dayOfMonth == null) return null;
  if (recurrence.kind == RecurrenceKind.monthly) {
    return _GhostSpec(RecurrenceKind.monthly, dayOfMonth: dayOfMonth);
  }
  final month = int.tryParse(fields[3]);
  if (month == null) return null;
  return _GhostSpec(
    RecurrenceKind.yearly,
    dayOfMonth: dayOfMonth,
    month: month,
  );
}

/// An immutable "does this calendar day match?" predicate for one of the
/// recurrence shapes the picker authors.
class _GhostSpec {
  const _GhostSpec(this.kind, {this.weekday, this.dayOfMonth, this.month});

  final RecurrenceKind kind;

  /// Weekly only (Dart convention Mon=1 … Sun=7).
  final int? weekday;

  /// Monthly + yearly.
  final int? dayOfMonth;

  /// Yearly only (1-12).
  final int? month;

  bool matches(DateTime day) => switch (kind) {
        RecurrenceKind.daily => true,
        RecurrenceKind.weekdays =>
          day.weekday >= DateTime.monday && day.weekday <= DateTime.friday,
        RecurrenceKind.weekly => day.weekday == weekday,
        RecurrenceKind.monthly => day.day == dayOfMonth,
        RecurrenceKind.yearly => day.day == dayOfMonth && day.month == month,
        // Unreachable: _ghostSpecFromCron rejects both before constructing.
        RecurrenceKind.none || RecurrenceKind.custom => false,
      };
}

/// [day] advanced by [count] CALENDAR days, staying at local midnight.
///
/// Deliberately not `day.add(Duration(days: n))`: a [Duration] is an exact
/// elapsed-time span, so adding 24h across Europe/Madrid's autumn roll-back
/// (2026-10-25) lands on 23:00 of the *same* day. Every subsequent step then
/// carries that hour, so the keys stop being local midnights — the calendar
/// looks ghosts up by `DateTime(y, m, d)` and would silently miss them, and
/// the range's final day would be dropped as "after the end". The
/// [DateTime] constructor normalizes an out-of-range day field into the
/// right calendar date instead, which is what a *calendar* projection means.
DateTime _addDays(DateTime day, int count) =>
    DateTime(day.year, day.month, day.day + count);

/// Splits a 5-field cron string into its fields, or null when it isn't
/// exactly 5 whitespace-separated fields.
List<String>? _cronFields(String cron) {
  final fields = cron.trim().split(RegExp(r'\s+'));
  return fields.length == 5 ? fields : null;
}

/// [instant]'s local calendar day — `.toLocal()` then drop the time. Unlike
/// [localDueDay] (which parses a `String?`), this takes an already-parsed
/// [DateTime] — used to resolve [expandRecurringForRange]'s `now` (defaulted
/// to [DateTime.now], which is already local, but a test-injected `now`
/// could be UTC-aware).
DateTime _localDay(DateTime instant) {
  final local = instant.toLocal();
  return DateTime(local.year, local.month, local.day);
}

/// Builds a `lowercased project name → "#RRGGBB"` lookup from [projects].
///
/// Projects without a color are skipped (they fall back to the kit accent at
/// render time). Names are lowercased so the match against `task.category` is
/// case-insensitive.
Map<String, String> projectColorMap(List<Project> projects) {
  final out = <String, String>{};
  for (final p in projects) {
    final color = p.color;
    if (color == null || color.isEmpty) continue;
    out[p.name.toLowerCase()] = color;
  }
  return out;
}

/// Resolves the accent [Color] for a [task] from its project's color.
///
/// Lowercases `task.category`, looks it up in [projectColorByName], parses the
/// stored hex, and returns it. Returns [fallback] when the task has no category,
/// the category has no matching project, or the stored hex is malformed.
Color colorForTask(
  Task task,
  Map<String, String> projectColorByName,
  Color fallback,
) {
  final category = task.category;
  if (category == null || category.isEmpty) return fallback;
  final hex = projectColorByName[category.toLowerCase()];
  if (hex == null) return fallback;
  return parseHexColor(hex) ?? fallback;
}

/// Splits [dayTasks] into how many are still **open** vs already **done**.
///
/// "Done" is sourced from [Task.isDone] (`status == 'done'`), so this stays in
/// lock-step with the rest of the app's completion semantics. Returns a record
/// so the marker + header builders can render the two cohorts distinctly
/// (filled dots for open, hollow for done) without re-scanning the list.
({int open, int done}) dayTaskCounts(List<Task> dayTasks) {
  var open = 0;
  var done = 0;
  for (final task in dayTasks) {
    if (task.isDone) {
      done++;
    } else {
      open++;
    }
  }
  return (open: open, done: done);
}

/// Whether **every** task due on a day is done — the signal for the "fully
/// cleared" day treatment (a check badge instead of dots).
///
/// Returns `true` only when there is at least one task and all of them are
/// done. An empty day is `false` (nothing to clear), and any single open task
/// keeps it `false`.
bool isDayAllDone(List<Task> dayTasks) {
  if (dayTasks.isEmpty) return false;
  return dayTasks.every((task) => task.isDone);
}

/// Chooses which of a day's [tasks] render as marker dots, plus the overflow
/// count for the trailing muted "+N".
///
/// Open tasks lead (the most actionable, drawn as filled project-colored dots);
/// done tasks trail (drawn as hollow rings). At most [maxDots] dots are shown
/// and the remainder collapses into [overflow]. Pure + immutable: the input is
/// never mutated — a fresh ordered list is returned — so a provider-owned list
/// can be passed straight in.
({List<Task> shown, int overflow}) pickDayMarkerTasks(
  List<Task> tasks, {
  required int maxDots,
}) {
  final open = <Task>[];
  final done = <Task>[];
  for (final task in tasks) {
    (task.isDone ? done : open).add(task);
  }
  final ordered = <Task>[...open, ...done];
  final shown = ordered.take(maxDots).toList();
  return (shown: shown, overflow: ordered.length - shown.length);
}

/// Combines a day's real [tasks] (via [pickDayMarkerTasks]) with its
/// recurrence [ghosts] into what the day's marker row should actually
/// render — the fix for the 2026-08 "every day says ○ ○ ○ +37" report,
/// where ~37 recurring tasks made every future day render identically and
/// carry zero information.
///
/// Ghosts are purely speculative — a "a repeat lands here" hint, never real
/// work — so two rules keep them from drowning out the real signal:
///  * They NEVER inflate [overflow]. That count is about real tasks the user
///    actually has this day; a ghost is not one of them.
///  * At most ONE ghost ever renders ([ghost], nullable), and only when a
///    dot slot is free after the real tasks ([maxDots] - `shown.length` > 0).
///    A row of N hollow rings says nothing more than a single one does.
///
/// When a slot is free, [ghost] is deterministically [ghosts].first (the
/// same task that would have led the old unbounded ghost row) — not a
/// random pick — so which task's color renders is stable across rebuilds.
({List<Task> shown, Task? ghost, int overflow}) pickDayMarkers(
  List<Task> tasks,
  List<Task> ghosts, {
  required int maxDots,
}) {
  final picked = pickDayMarkerTasks(tasks, maxDots: maxDots);
  final hasGhostSlot = picked.shown.length < maxDots;
  final ghost = hasGhostSlot && ghosts.isNotEmpty ? ghosts.first : null;
  return (shown: picked.shown, ghost: ghost, overflow: picked.overflow);
}

/// Parses a `"#RRGGBB"` (or bare `"RRGGBB"` / `"#AARRGGBB"`) hex string into an
/// opaque [Color]. Returns null when the string isn't a valid hex color.
Color? parseHexColor(String hex) {
  var h = hex.trim();
  if (h.startsWith('#')) h = h.substring(1);
  if (h.length == 6) h = 'FF$h'; // assume fully opaque
  if (h.length != 8) return null;
  final value = int.tryParse(h, radix: 16);
  if (value == null) return null;
  return Color(value);
}
