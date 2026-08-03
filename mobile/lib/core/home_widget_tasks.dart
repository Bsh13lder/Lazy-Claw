import 'package:home_widget/home_widget.dart';

import '../models/task.dart';
import 'due_date.dart';

/// Writes the "Today tasks" home-screen widget snapshot.
///
/// CRYPTO BOUNDARY: this is the ONLY place task data crosses out of the app's
/// AES boundary into plaintext `HomeWidget` SharedPreferences (which the Android
/// widget host process reads). To keep the leak minimal we export only a task's
/// TITLE plus a short DUE LABEL — never notes/description, never ids. The widget
/// is a read-only glance + a couple of deep-link buttons.
///
/// Storage shape (keys read by `TasksWidget.kt`):
///   * `task_count`   — int, the number of rows actually written (0..3).
///   * `task_<i>_title` / `task_<i>_due` — the i-th row (i = 0,1,2).
///   * `task_more`    — footer label ('+2 more') when open tasks overflow
///                      the rows, else '' (footer hidden).
///   * `task_stamp`   — `HH:mm` of the last snapshot write (header freshness
///                      stamp; '' until the first write / after logout).
///
/// Every method is best-effort and NEVER throws: a platform without the plugin,
/// a denied widget, or a serialization hiccup simply leaves the widget stale.

/// How many task rows the widget shows.
const int kTasksWidgetRowCount = 3;

const String _kCountKey = 'task_count';

/// Pick the top [kTasksWidgetRowCount] tasks from the widget relevance tier
/// (overdue+today, else upcoming, else undated — see [relevantWidgetTasks])
/// and push a plaintext snapshot to the `TasksWidget` home-screen widget.
/// Fire-and-forget; never throws.
Future<void> updateTasksWidget(List<Task> tasks, {DateTime? now}) async {
  try {
    final tier = relevantWidgetTasks(tasks, now: now);
    final picked = tier.take(kTasksWidgetRowCount).toList();
    await HomeWidget.saveWidgetData<int>(_kCountKey, picked.length);
    for (var i = 0; i < kTasksWidgetRowCount; i++) {
      final title = i < picked.length ? _short(picked[i].title) : '';
      final due = i < picked.length ? widgetDueLabel(picked[i], now: now) : '';
      await HomeWidget.saveWidgetData<String>('task_${i}_title', title);
      await HomeWidget.saveWidgetData<String>('task_${i}_due', due);
    }
    // "+N more" counts EVERY open task not shown — dated and undated alike —
    // so a hidden future-dated task always produces a hint (never silently
    // starved out by the 3-row cap, see relevantWidgetTasks below).
    await HomeWidget.saveWidgetData<String>(
      'task_more', widgetMoreLabel(tier.length),
    );
    // Freshness stamp shown in the widget header — doubles as the frozen-
    // widget diagnostic (stamp moving = paint path alive).
    await HomeWidget.saveWidgetData<String>(
      'task_stamp', widgetUpdatedStamp(now ?? DateTime.now()),
    );
    await HomeWidget.updateWidget(
      name: 'TasksWidget',
      androidName: 'TasksWidget',
    );
  } catch (_) {
    // Best-effort: a widget-write failure must never break a task write.
  }
}

/// Zero the snapshot (count = 0, blank rows) and repaint — used on logout so a
/// signed-out device never shows the previous user's task titles. Never throws.
Future<void> clearTasksWidget() async {
  try {
    await HomeWidget.saveWidgetData<int>(_kCountKey, 0);
    for (var i = 0; i < kTasksWidgetRowCount; i++) {
      await HomeWidget.saveWidgetData<String>('task_${i}_title', '');
      await HomeWidget.saveWidgetData<String>('task_${i}_due', '');
    }
    await HomeWidget.saveWidgetData<String>('task_more', '');
    await HomeWidget.saveWidgetData<String>('task_stamp', '');
    await HomeWidget.updateWidget(
      name: 'TasksWidget',
      androidName: 'TasksWidget',
    );
  } catch (_) {
    // Best-effort.
  }
}

// ── Pure selection / labeling (no plugin — unit-testable) ────────────────────

/// EVERY open task the "Today tasks" widget draws from, in priority order —
/// mirrors the home screen's Today section so the widget honors its own
/// title while never hiding the rest of the user's work:
///   1. overdue + due-today tasks, soonest first (the headline set);
///   2. then upcoming dated tasks, soonest first (so a spare row always
///      surfaces the next thing due — the date pill says "Tomorrow" /
///      "Jun 15" so a row can't masquerade as today's);
///   3. then open undated tasks, in their incoming order.
/// Done tasks are excluded everywhere. Pure + deterministic via [now].
///
/// Uncapped and NEVER drops a task — [pickWidgetTasks] caps the ROWS, but
/// this full list is also what [widgetMoreLabel] counts, so a task pushed
/// past the row cap (dated OR undated) always contributes to "+N more"
/// instead of vanishing with no hint (the widget only ever had 3 rows;
/// previously a full `dueNow` tier hid every future-dated task with zero
/// footer, since `widgetMoreLabel` only saw that one tier's length).
List<Task> relevantWidgetTasks(List<Task> tasks, {DateTime? now}) {
  final ref = now ?? DateTime.now();
  final today = DateTime(ref.year, ref.month, ref.day);

  final dueNow = <Task>[]; // overdue + today
  final upcoming = <Task>[];
  final undated = <Task>[];
  for (final t in tasks) {
    if (t.isDone) continue;
    final instant = _dueInstant(t.dueDate);
    if (instant == null) {
      undated.add(t);
      continue;
    }
    // `instant` is already `.toLocal()`d (see `_dueInstant`) — bucketing by
    // the LOCAL day matches the calendar's `groupTasksByDay` convention
    // (D1): a UTC-aware due date must not be misread as "today" or
    // "overdue" one day early/late.
    final dueDay = DateTime(instant.year, instant.month, instant.day);
    if (dueDay.isAfter(today)) {
      upcoming.add(t);
    } else {
      dueNow.add(t);
    }
  }
  int byDue(Task a, Task b) =>
      _dueInstant(a.dueDate)!.compareTo(_dueInstant(b.dueDate)!);
  dueNow.sort(byDue);
  upcoming.sort(byDue);
  return [...dueNow, ...upcoming, ...undated];
}

/// The OPEN tasks to surface in the widget rows: due-now first, then
/// upcoming (soonest first), then undated fill any rows still spare —
/// the first [kTasksWidgetRowCount] of [relevantWidgetTasks].
List<Task> pickWidgetTasks(List<Task> tasks, {DateTime? now}) =>
    relevantWidgetTasks(tasks, now: now).take(kTasksWidgetRowCount).toList();

/// The due label shown beside a task title in the widget — the task's full
/// date and time, compact:
///   * timed due → `Today · 5:00 PM` / `Tomorrow · 9:00 AM` / `Jun 15 · 5:30 PM`;
///   * date-only due → `Today` / `Tomorrow` / `Yesterday` / `Jun 9`;
///   * no due → empty string.
/// Pure; [now] drives the relative wording.
String widgetDueLabel(Task task, {DateTime? now}) {
  final due = task.dueDate;
  if (due == null || due.isEmpty) return '';
  // `.toLocal()` before reading the calendar day — matches `_dueInstant` and
  // `groupTasksByDay` (D1): a UTC-aware due date must resolve to the same
  // wall-clock day the row's own chip shows (`due_date.dart:31`).
  final date = DateTime.tryParse(due)?.toLocal();
  if (date == null) return '';
  final day = _dayWord(date, now ?? DateTime.now());
  if (dueDateHasTime(due)) {
    final parts = dueTimeParts(due);
    if (parts == null) return day;
    return '$day · ${formatClock12(parts.hour, parts.minute)}';
  }
  return day;
}

/// Relative day word for [date] vs [ref]: `Today` / `Tomorrow` / `Yesterday`,
/// else `Jun 15`. Pure.
String _dayWord(DateTime date, DateTime ref) {
  final dueDay = DateTime(date.year, date.month, date.day);
  final today = DateTime(ref.year, ref.month, ref.day);
  final delta = dueDay.difference(today).inDays;
  if (delta == 0) return 'Today';
  if (delta == 1) return 'Tomorrow';
  if (delta == -1) return 'Yesterday';
  return '${_monthAbbrev(date.month)} ${date.day}';
}

/// The 24h `HH:mm` freshness stamp shown in the widget header. Pure.
String widgetUpdatedStamp(DateTime now) =>
    '${now.hour.toString().padLeft(2, '0')}:'
    '${now.minute.toString().padLeft(2, '0')}';

/// The footer overflow label: '' when everything fits the rows, else
/// '+N more' for the open tasks beyond [kTasksWidgetRowCount]. Pure.
String widgetMoreLabel(int openCount) {
  final extra = openCount - kTasksWidgetRowCount;
  return extra > 0 ? '+$extra more' : '';
}

/// Parse a `dueDate` (either shape) to a comparable **local** instant, or
/// null when absent/unparseable. Date-only sorts as that day at 00:00 local —
/// fine for ordering against timed dues. `.toLocal()` is a no-op for an
/// already-local/naive value and only corrects a UTC-aware one (D1); sort
/// order (`byDue`'s `compareTo`) is unaffected either way since it compares
/// the same absolute instant regardless of the UTC/local flag.
DateTime? _dueInstant(String? due) {
  if (due == null || due.isEmpty) return null;
  return DateTime.tryParse(due)?.toLocal();
}

/// Clamp a title to a sane widget length so a pathological title can't bloat
/// the plaintext snapshot (defense-in-depth on the crypto boundary).
String _short(String title) {
  const max = 60;
  final t = title.trim();
  return t.length <= max ? t : '${t.substring(0, max - 1)}…';
}

const List<String> _kMonthAbbrev = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

String _monthAbbrev(int month) =>
    (month >= 1 && month <= 12) ? _kMonthAbbrev[month - 1] : '';
