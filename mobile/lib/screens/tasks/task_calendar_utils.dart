import 'package:flutter/material.dart';

import '../../models/project.dart';
import '../../models/task.dart';

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
      continue; // Non-ISO / malformed → not plottable.
    }
    final key = DateTime(parsed.year, parsed.month, parsed.day);
    (out[key] ??= <Task>[]).add(task);
  }
  return out;
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
