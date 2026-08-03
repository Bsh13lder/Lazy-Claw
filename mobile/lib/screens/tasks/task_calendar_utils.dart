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

/// Safety valve for [expandRecurringForRange]: the most ghost days a single
/// recurring task will ever project, regardless of how wide [rangeStart] ..
/// [rangeEnd] is. Guards against a pathological cron (or a caller passing a
/// multi-year range) generating an unbounded number of entries.
const int kMaxGhostsPerTask = 60;

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
/// Ghosts are a forward-looking "here's the next repeat" hint, never a
/// history — no ghost is ever generated for a day before [now]'s local
/// calendar day (defaults to the wall clock; pass it explicitly for
/// deterministic tests). Paging the calendar back to a month before the
/// task existed must not paint ghost dots on every matching day in that
/// month, which would read as phantom repeats that never happened. A ghost
/// exactly on today IS allowed — today isn't "the past" yet — and the
/// real-vs-ghost dedup above still applies on top of this clamp.
///
/// Capped at [kMaxGhostsPerTask] generated ghosts per task.
Map<DateTime, List<Task>> expandRecurringForRange(
  List<Task> tasks,
  DateTime rangeStart,
  DateTime rangeEnd, {
  DateTime? now,
}) {
  final start = DateTime(rangeStart.year, rangeStart.month, rangeStart.day);
  final end = DateTime(rangeEnd.year, rangeEnd.month, rangeEnd.day);
  final out = <DateTime, List<Task>>{};
  if (end.isBefore(start)) return out;

  final today = _localDay(now ?? DateTime.now());
  final loopStart = start.isBefore(today) ? today : start;
  if (loopStart.isAfter(end)) return out;

  for (final task in tasks) {
    final cron = task.recurring;
    if (cron == null || cron.trim().isEmpty) continue;

    final recurrence = recurrenceFromCron(cron);
    if (!recurrence.repeats || recurrence.kind == RecurrenceKind.custom) {
      continue; // Unparseable / unsupported shape → no ghosts.
    }

    // Monthly/yearly need the exact day-of-month (and, for yearly, month)
    // straight off the cron string — `Recurrence` only carries `kind` +
    // `weekday`, it doesn't retain those fields.
    int? monthlyDay;
    int? yearlyDay;
    int? yearlyMonth;
    if (recurrence.kind == RecurrenceKind.monthly ||
        recurrence.kind == RecurrenceKind.yearly) {
      final fields = _cronFields(cron);
      if (fields == null) continue;
      final dom = int.tryParse(fields[2]);
      if (dom == null) continue;
      if (recurrence.kind == RecurrenceKind.monthly) {
        monthlyDay = dom;
      } else {
        final mon = int.tryParse(fields[3]);
        if (mon == null) continue;
        yearlyDay = dom;
        yearlyMonth = mon;
      }
    }

    final realDay = localDueDay(task.dueDate);

    var day = loopStart;
    var generated = 0;
    while (!day.isAfter(end) && generated < kMaxGhostsPerTask) {
      final matches = switch (recurrence.kind) {
        RecurrenceKind.daily => true,
        RecurrenceKind.weekdays =>
          day.weekday >= DateTime.monday && day.weekday <= DateTime.friday,
        RecurrenceKind.weekly => day.weekday == recurrence.weekday,
        RecurrenceKind.monthly => day.day == monthlyDay,
        RecurrenceKind.yearly =>
          day.day == yearlyDay && day.month == yearlyMonth,
        RecurrenceKind.none || RecurrenceKind.custom => false, // unreachable
      };
      if (matches && day != realDay) {
        (out[day] ??= <Task>[]).add(task);
        generated++;
      }
      day = day.add(const Duration(days: 1));
    }
  }
  return out;
}

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
