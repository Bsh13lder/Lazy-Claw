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
///
/// Every method is best-effort and NEVER throws: a platform without the plugin,
/// a denied widget, or a serialization hiccup simply leaves the widget stale.

/// How many task rows the widget shows.
const int kTasksWidgetRowCount = 3;

const String _kCountKey = 'task_count';

/// Pick the top [kTasksWidgetRowCount] OPEN tasks, soonest first (overdue/today
/// ahead of upcoming, undated last), and push a plaintext snapshot to the
/// `TasksWidget` home-screen widget. Fire-and-forget; never throws.
Future<void> updateTasksWidget(List<Task> tasks, {DateTime? now}) async {
  try {
    final picked = pickWidgetTasks(tasks, now: now);
    final openCount = tasks.where((t) => !t.isDone).length;
    await HomeWidget.saveWidgetData<int>(_kCountKey, picked.length);
    for (var i = 0; i < kTasksWidgetRowCount; i++) {
      final title = i < picked.length ? _short(picked[i].title) : '';
      final due = i < picked.length ? widgetDueLabel(picked[i], now: now) : '';
      await HomeWidget.saveWidgetData<String>('task_${i}_title', title);
      await HomeWidget.saveWidgetData<String>('task_${i}_due', due);
    }
    await HomeWidget.saveWidgetData<String>(
      'task_more', widgetMoreLabel(openCount),
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
    await HomeWidget.updateWidget(
      name: 'TasksWidget',
      androidName: 'TasksWidget',
    );
  } catch (_) {
    // Best-effort.
  }
}

// ── Pure selection / labeling (no plugin — unit-testable) ────────────────────

/// The OPEN tasks to surface, soonest-due first, capped at
/// [kTasksWidgetRowCount]. Ordering:
///   1. tasks WITH a due date, ascending by due instant (overdue & today come
///      first naturally because they're the smallest instants);
///   2. then tasks with NO due date, in their incoming order.
/// Done tasks are excluded. Pure + deterministic ([now] is unused today but
/// kept for symmetry with the labeler and future "hide far-future" tuning).
List<Task> pickWidgetTasks(List<Task> tasks, {DateTime? now}) {
  final open = tasks.where((t) => !t.isDone).toList();
  final dated = <Task>[];
  final undated = <Task>[];
  for (final t in open) {
    if (_dueInstant(t.dueDate) != null) {
      dated.add(t);
    } else {
      undated.add(t);
    }
  }
  // Stable sort the dated ones by their due instant (earliest first).
  dated.sort((a, b) =>
      _dueInstant(a.dueDate)!.compareTo(_dueInstant(b.dueDate)!));
  final ordered = <Task>[...dated, ...undated];
  return ordered.take(kTasksWidgetRowCount).toList();
}

/// The short due label shown under a task title in the widget:
///   * timed due → `5:00 PM` (clock only — the day is implied "soon");
///   * date-only due → `Today` / `Tomorrow` / `Jun 9`;
///   * no due → empty string.
/// Pure; [now] drives the relative wording.
String widgetDueLabel(Task task, {DateTime? now}) {
  final due = task.dueDate;
  if (dueDateHasTime(due)) {
    final parts = dueTimeParts(due);
    if (parts != null) return formatClock12(parts.hour, parts.minute);
    return '';
  }
  if (due != null && due.length == 10) {
    final date = DateTime.tryParse(due);
    if (date == null) return '';
    final dueDay = DateTime(date.year, date.month, date.day);
    final ref = now ?? DateTime.now();
    final today = DateTime(ref.year, ref.month, ref.day);
    final delta = dueDay.difference(today).inDays;
    if (delta == 0) return 'Today';
    if (delta == 1) return 'Tomorrow';
    if (delta == -1) return 'Yesterday';
    return '${_monthAbbrev(date.month)} ${date.day}';
  }
  return '';
}

/// The footer overflow label: '' when everything fits the rows, else
/// '+N more' for the open tasks beyond [kTasksWidgetRowCount]. Pure.
String widgetMoreLabel(int openCount) {
  final extra = openCount - kTasksWidgetRowCount;
  return extra > 0 ? '+$extra more' : '';
}

/// Parse a `dueDate` (either shape) to a comparable instant, or null when
/// absent/unparseable. Date-only sorts as that day at 00:00 local — fine for
/// ordering against timed dues.
DateTime? _dueInstant(String? due) {
  if (due == null || due.isEmpty) return null;
  return DateTime.tryParse(due);
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
