import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:timezone/timezone.dart' as tz;

import '../../models/task.dart';
import '../../notifications/notification_actions.dart';
import '../due_date.dart';
import '../reminder_offset.dart';

/// Abstract seam the tasks provider depends on, so scheduling can be wired in
/// production yet left null/faked in tests without a hard plugin dependency.
abstract class TaskReminderScheduler {
  /// (Re)schedule the local reminder for [task]. Cancels any prior schedule
  /// first, then schedules iff the task is open and has a future fire time.
  Future<void> scheduleForTask(Task task);

  /// Cancel any scheduled reminder for the task with [taskId].
  Future<void> cancelForTask(String taskId);

  /// Reconcile the whole scheduled set against [tasks]: cancel stale entries
  /// (deleted / completed / time-removed) and (re)schedule the live ones.
  Future<void> syncAll(List<Task> tasks);
}

// ── Pure decision functions (no plugin, no Flutter — trivially unit-testable) ─

/// The instant a reminder should fire for [task], or null when nothing should
/// be scheduled.
///
/// Candidate sources, in precedence order — the FIRST one still in the future
/// wins (a candidate that's already in the past is SKIPPED, not fatal):
///   1. An explicit [Task.reminderAt].
///   2. The task's [Task.dueDate] WITH a time-of-day (`…THH:mm:ss`).
///   3. A DATE-ONLY due (`yyyy-MM-dd`) → that calendar date at the default
///      reminder time-of-day ([defaultReminderHour]:[defaultReminderMinute],
///      09:00 by default) — this is what makes ordinary "due tomorrow" tasks
///      (which carry no time) fire a reminder.
///
/// Falling THROUGH a past candidate is deliberate: the app auto-applies a 30-min
/// lead, so a task created <30 min before it's due gets a `reminderAt` already
/// in the past — that must NOT suppress the still-future due-time reminder, or
/// the task would fire nothing at all. Returns null only when EVERY candidate is
/// absent or already passed.
DateTime? reminderFireTime(
  Task task, {
  DateTime? now,
  int defaultReminderHour = 9,
  int defaultReminderMinute = 0,
}) {
  final clock = now ?? DateTime.now();
  final candidates = <DateTime?>[
    _parseReminderAt(task.reminderAt),
    _parseDueWithTime(task.dueDate),
    _parseDueDateOnlyAt(task.dueDate, defaultReminderHour, defaultReminderMinute),
  ];
  for (final candidate in candidates) {
    if (candidate != null && candidate.isAfter(clock)) return candidate;
  }
  return null;
}

DateTime? _parseReminderAt(String? reminderAt) {
  if (reminderAt == null || reminderAt.isEmpty) return null;
  return DateTime.tryParse(reminderAt);
}

DateTime? _parseDueWithTime(String? due) {
  if (!dueDateHasTime(due)) return null;
  return DateTime.tryParse(due!);
}

/// Parse a DATE-ONLY due (`yyyy-MM-dd`, no time-of-day) and pin it to [h]:[m]
/// local. Returns null unless [due] is present, has NO time component, and its
/// date part parses — so a timed due or a missing due never reaches this path
/// (those are handled by the earlier precedence steps). This is the fallback
/// that lets ordinary date-only tasks ("due tomorrow") fire a reminder.
DateTime? _parseDueDateOnlyAt(String? due, int h, int m) {
  if (due == null || dueDateHasTime(due)) return null;
  // Date-only is the canonical `yyyy-MM-dd` (length 10). Parse just the date
  // part so a trailing stray character can't silently shift the day.
  if (due.length != 10) return null;
  final date = DateTime.tryParse(due);
  if (date == null) return null;
  return DateTime(date.year, date.month, date.day, h, m);
}

/// The EVENT instant that the user's reminder OFFSETS are measured from, or null
/// when there's nothing to anchor to.
///
/// Precedence (DUE-first — an offset is a lead BEFORE the event, so the event is
/// the due time, NOT the legacy single [Task.reminderAt] lead):
///   1. A timed due (`…THH:mm:ss`) → that instant.
///   2. A date-only due (`yyyy-MM-dd`) → that date at the default time-of-day.
///   3. No due at all → the explicit [Task.reminderAt] instant (offsets then key
///      off that, so a reminder-only task still fires).
///
/// Unlike [reminderFireTime], this does NOT past-filter — that's done at
/// schedule time by [computeOffsetFireTimes]. Pure.
DateTime? reminderBaseTime(
  Task task, {
  int defaultReminderHour = 9,
  int defaultReminderMinute = 0,
}) {
  final timed = _parseDueWithTime(task.dueDate);
  if (timed != null) return timed;
  final dateOnly =
      _parseDueDateOnlyAt(task.dueDate, defaultReminderHour, defaultReminderMinute);
  if (dateOnly != null) return dateOnly;
  return _parseReminderAt(task.reminderAt);
}

/// Derive a stable, positive 31-bit notification id from a task id.
///
/// Uses FNV-1a (32-bit) so the mapping is deterministic across runs, isolates
/// and platforms — unlike `String.hashCode`, which Dart does not guarantee to
/// be stable. `zonedSchedule`/`cancel` take a 32-bit int id, so we mask to a
/// positive 31-bit range.
int notificationIdForTask(String taskId) {
  var hash = 0x811c9dc5; // FNV-1a 32-bit offset basis
  for (final unit in taskId.codeUnits) {
    hash ^= unit;
    hash = (hash * 0x01000193) & 0xFFFFFFFF; // FNV prime, kept 32-bit
  }
  return hash & 0x7FFFFFFF; // positive 31-bit
}

/// The maximum number of distinct offset reminders a single task can schedule.
/// Caps the notification-id range reserved (and swept on cancel/reconcile) per
/// task, so a runaway offset list can't exhaust ids or slow cancellation.
const int kMaxOffsetReminders = 16;

/// The notification id for the [index]-th offset reminder of [taskId].
///
/// Index 0 deliberately COINCIDES with [notificationIdForTask] so the
/// single-reminder fallback (empty offsets) and any legacy single-id schedule
/// share one id — no orphaned alarm when a user switches between the two. Higher
/// indices derive a distinct stable FNV id from a per-index key.
int notificationIdForOffset(String taskId, int index) =>
    index == 0 ? notificationIdForTask(taskId) : notificationIdForTask('$taskId#$index');

/// The notification BODY text — it describes WHEN THE TASK IS DUE, never the
/// instant the reminder pops. A reminder may fire ahead of the due time (an
/// auto-applied lead) or at a default 09:00 for a date-only task, so the
/// fire-time would be misleading; we derive the wording from [Task.dueDate]:
///
///   * timed due (`…THH:mm:ss`) → `Due at 5:00 PM` (the DUE clock time).
///   * date-only due (`yyyy-MM-dd`) → `Due today` / `Due tomorrow` /
///     `Due Jun 9` — a day phrase only, NEVER an invented clock time.
///   * no due but an explicit [Task.reminderAt] → a generic `Reminder`.
///   * nothing schedulable → `Reminder` (defensive; callers only schedule when
///     [reminderFireTime] is non-null).
///
/// Pure — [now] is injected so day-relative wording ("today"/"tomorrow") is
/// deterministic in tests.
String bodyForReminder(Task task, {DateTime? now}) {
  final due = task.dueDate;
  // 1. Timed due → the task's own clock time (NOT the fire time).
  if (dueDateHasTime(due)) {
    final parts = dueTimeParts(due);
    if (parts != null) {
      return 'Due at ${formatClock12(parts.hour, parts.minute)}';
    }
    // A `T`-bearing but unparseable due falls through to the date / generic
    // paths below rather than inventing a time.
  }
  // 2. Date-only due → a day phrase, never a clock time.
  if (due != null && due.length == 10 && !dueDateHasTime(due)) {
    final dayLabel = _dueDayLabel(due, now ?? DateTime.now());
    if (dayLabel != null) return dayLabel;
  }
  // 3. No usable due, but an explicit reminder was set → generic.
  // 4. Fallback (defensive) → generic.
  return 'Reminder';
}

/// `Due today` / `Due tomorrow` / `Due Jun 9` for a `yyyy-MM-dd` [due], using
/// the calendar day of [now] for the relative wording. Returns null when [due]
/// doesn't parse as a date.
String? _dueDayLabel(String due, DateTime now) {
  final date = DateTime.tryParse(due);
  if (date == null) return null;
  final dueDay = DateTime(date.year, date.month, date.day);
  final today = DateTime(now.year, now.month, now.day);
  final deltaDays = dueDay.difference(today).inDays;
  if (deltaDays == 0) return 'Due today';
  if (deltaDays == 1) return 'Due tomorrow';
  return 'Due ${_monthAbbrev(date.month)} ${date.day}';
}

const List<String> _kMonthAbbrev = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/// `Jan`..`Dec` for a 1-12 [month]; intl-free so it's deterministic in tests.
String _monthAbbrev(int month) =>
    (month >= 1 && month <= 12) ? _kMonthAbbrev[month - 1] : '';

// ── Scheduling seam (testable) ───────────────────────────────────────────────

/// Minimal notification-scheduling seam so [TaskReminderService] can be unit-
/// tested without a real plugin / platform channel. Production wires the
/// [FlutterLocalNotificationsSink] adapter; tests inject a recording fake.
///
/// Every method is best-effort and MUST NOT throw — the service treats a failed
/// schedule/cancel as "no reminder" rather than letting it break a task write.
abstract class ReminderSink {
  /// Cancel the scheduled notification with [id].
  Future<void> cancel(int id);

  /// Schedule a notification with [id] to fire at the local wall-clock [fire]
  /// instant, carrying [title] / [body] / [payload].
  Future<void> schedule({
    required int id,
    required String title,
    required String body,
    required DateTime fire,
    required String payload,
  });

  /// The ids of all currently-pending scheduled notifications.
  Future<List<int>> pendingIds();
}

/// Production [ReminderSink] backed by the shared [FlutterLocalNotificationsPlugin].
///
/// Owns the notification channel + presentation details + the DST-correct tz
/// conversion + the exact→inexact alarm fallback, so [TaskReminderService] stays
/// pure scheduling logic. Every method swallows its own errors (denied
/// permission, uninitialised tz db, missing plugin) → a silent no-op.
class FlutterLocalNotificationsSink implements ReminderSink {
  FlutterLocalNotificationsSink(this._plugin);

  final FlutterLocalNotificationsPlugin _plugin;

  /// Dedicated channel so task reminders are distinct from the background-task
  /// / approval notifications that go through `lazyclaw_tasks`.
  static const String channelId = 'lazyclaw_task_reminders';
  static const String _channelName = 'Task reminders';
  static const String _channelDesc =
      'Scheduled reminders for your tasks at their due / reminder time';

  static const NotificationDetails _details = NotificationDetails(
    android: AndroidNotificationDetails(
      channelId,
      _channelName,
      channelDescription: _channelDesc,
      importance: Importance.high,
      priority: Priority.high,
      category: AndroidNotificationCategory.reminder,
      showWhen: true,
      // White claw silhouette (system-tinted) + full-colour logo as the big icon.
      icon: 'ic_stat_lazyclaw',
      largeIcon: DrawableResourceAndroidBitmap('@mipmap/ic_launcher'),
      // "Done" button completes the task straight from the shade — handled by
      // [notificationBackgroundHandler] (killed app) or [_onResponse] (warm).
      // `showsUserInterface: false` keeps it a background action (no app launch);
      // `cancelNotification: true` dismisses the reminder on tap. The reminder's
      // `payload: 'task:<id>'` carries the id the handler completes.
      actions: <AndroidNotificationAction>[
        AndroidNotificationAction(
          kTaskDoneActionId,
          'Done',
          showsUserInterface: false,
          cancelNotification: true,
        ),
      ],
    ),
    iOS: DarwinNotificationDetails(
      presentAlert: true,
      presentBadge: true,
      presentSound: true,
    ),
    macOS: DarwinNotificationDetails(
      presentAlert: true,
      presentBadge: true,
      presentSound: true,
    ),
  );

  @override
  Future<void> cancel(int id) async {
    try {
      await _plugin.cancel(id);
    } catch (_) {
      // Best-effort.
    }
  }

  @override
  Future<List<int>> pendingIds() async {
    try {
      final pending = await _plugin.pendingNotificationRequests();
      return [for (final p in pending) p.id];
    } catch (_) {
      return const [];
    }
  }

  @override
  Future<void> schedule({
    required int id,
    required String title,
    required String body,
    required DateTime fire,
    required String payload,
  }) async {
    try {
      // Build the instant from the fire time's wall-clock COMPONENTS in the
      // local zone, so the zone's DST rules for THAT calendar date apply.
      // tz.TZDateTime.from(naiveLocal) converts via the CURRENT utc offset, so a
      // reminder straddling a DST boundary would land an hour off. (Throws if the
      // tz db isn't initialised — the outer catch makes that a silent no-op.)
      final when = tz.TZDateTime(
        tz.local,
        fire.year,
        fire.month,
        fire.day,
        fire.hour,
        fire.minute,
        fire.second,
      );
      try {
        await _plugin.zonedSchedule(
          id,
          title,
          body,
          when,
          _details,
          androidScheduleMode: AndroidScheduleMode.exactAllowWhileIdle,
          uiLocalNotificationDateInterpretation:
              UILocalNotificationDateInterpretation.absoluteTime,
          payload: payload,
        );
      } on Exception {
        // Exact alarms blocked (Android 12+ without "Alarms & reminders") → fall
        // back to an inexact alarm so the reminder still fires (slightly late)
        // instead of being dropped entirely.
        await _plugin.zonedSchedule(
          id,
          title,
          body,
          when,
          _details,
          androidScheduleMode: AndroidScheduleMode.inexactAllowWhileIdle,
          uiLocalNotificationDateInterpretation:
              UILocalNotificationDateInterpretation.absoluteTime,
          payload: payload,
        );
      }
    } catch (_) {
      // Best-effort: never let a scheduling failure break the task write.
    }
  }
}

// ── Production implementation ────────────────────────────────────────────────

/// Schedules task reminders through a [ReminderSink].
///
/// "Unlimited reminders" (Proton-Calendar style): each task with a due time
/// schedules ONE notification per configured [offsets] entry — e.g. offsets
/// `['-1d', '-30m', '0m']` fire three reminders. When [offsets] is empty the
/// service falls back to the single legacy reminder at [reminderFireTime] so
/// nothing regresses. Every method is best-effort and never throws.
class TaskReminderService implements TaskReminderScheduler {
  TaskReminderService(
    this._sink, {
    int defaultReminderMinutes = 540,
    List<String>? offsets,
  })  : _defaultReminderMinutes = _clampMinutes(defaultReminderMinutes),
        _offsets = normalizeReminderOffsets(offsets ?? kDefaultReminderOffsets);

  final ReminderSink _sink;

  /// Minutes-from-midnight time-of-day used when a task is DATE-ONLY (no
  /// `THH:mm`). Defaults to 540 (09:00). Settable so the user's "Default
  /// reminder time" pref flows in live via [taskReminderServiceProvider]; a
  /// change takes effect on the next (re)schedule / [syncAll].
  int _defaultReminderMinutes;

  set defaultReminderMinutes(int minutes) =>
      _defaultReminderMinutes = _clampMinutes(minutes);

  int get defaultReminderMinutes => _defaultReminderMinutes;

  int get _defaultHour => _defaultReminderMinutes ~/ 60;
  int get _defaultMinute => _defaultReminderMinutes % 60;

  /// The user's default reminder OFFSET set (wire strings). Empty → the legacy
  /// single-reminder fallback. Settable so the Settings multi-select flows in
  /// live via [taskReminderServiceProvider]; a change takes effect on the next
  /// (re)schedule / [syncAll]. Always stored normalised (canonical, de-duped,
  /// earliest-fire-first).
  List<String> _offsets;

  set offsets(List<String> value) => _offsets = normalizeReminderOffsets(value);

  List<String> get offsets => List.unmodifiable(_offsets);

  /// Keep the configured time-of-day inside a valid day (0..1439). An absurd
  /// value can't push the fallback onto another calendar day.
  static int _clampMinutes(int minutes) =>
      minutes < 0 ? 0 : (minutes > 1439 ? 1439 : minutes);

  /// The (notificationId, fireInstant) pairs to schedule for [task] given the
  /// current [offsets]. Empty when nothing should fire (done task, no anchor, or
  /// every fire already elapsed). Capped at [kMaxOffsetReminders].
  List<({int id, DateTime fire})> _plannedFor(Task task) {
    if (task.isDone) return const [];
    final List<DateTime> fires;
    if (_offsets.isEmpty) {
      // Legacy single reminder (respects a pre-baked reminderAt lead).
      final f = reminderFireTime(
        task,
        defaultReminderHour: _defaultHour,
        defaultReminderMinute: _defaultMinute,
      );
      fires = f == null ? const [] : <DateTime>[f];
    } else {
      final base = reminderBaseTime(
        task,
        defaultReminderHour: _defaultHour,
        defaultReminderMinute: _defaultMinute,
      );
      if (base == null) {
        final f = reminderFireTime(
          task,
          defaultReminderHour: _defaultHour,
          defaultReminderMinute: _defaultMinute,
        );
        fires = f == null ? const [] : <DateTime>[f];
      } else {
        fires = computeOffsetFireTimes(base: base, offsets: _offsets);
      }
    }
    final planned = <({int id, DateTime fire})>[];
    for (var i = 0; i < fires.length && i < kMaxOffsetReminders; i++) {
      planned.add((id: notificationIdForOffset(task.id, i), fire: fires[i]));
    }
    return planned;
  }

  /// Every notification id this service could ever have used for [taskId] — the
  /// full reserved slot range. Cancelling the whole range on (re)schedule /
  /// cancel guarantees no stale alarm survives an offset-set change.
  Iterable<int> _reservedIds(String taskId) sync* {
    for (var i = 0; i < kMaxOffsetReminders; i++) {
      yield notificationIdForOffset(taskId, i);
    }
  }

  @override
  Future<void> scheduleForTask(Task task) async {
    try {
      // Clear the WHOLE reserved range first, so an edit that moves/removes the
      // time or shrinks the offset set can't leave a stale alarm behind.
      for (final id in _reservedIds(task.id)) {
        await _sink.cancel(id);
      }
      if (task.isDone) return;
      final planned = _plannedFor(task);
      if (planned.isEmpty) return;

      final title = task.title.isEmpty ? 'Task reminder' : task.title;
      final body = bodyForReminder(task, now: DateTime.now());
      final payload = 'task:${task.id}';
      for (final p in planned) {
        await _sink.schedule(
          id: p.id,
          title: title,
          body: body,
          fire: p.fire,
          payload: payload,
        );
      }
    } catch (_) {
      // Best-effort: never let a scheduling failure break the task write.
    }
  }

  @override
  Future<void> cancelForTask(String taskId) async {
    try {
      for (final id in _reservedIds(taskId)) {
        await _sink.cancel(id);
      }
    } catch (_) {
      // Best-effort.
    }
  }

  @override
  Future<void> syncAll(List<Task> tasks) async {
    try {
      // The set of ids that SHOULD remain scheduled across all live tasks.
      final desired = <int>{};
      final toSchedule = <Task>[];
      for (final t in tasks) {
        final planned = _plannedFor(t);
        if (planned.isEmpty) continue;
        for (final p in planned) {
          desired.add(p.id);
        }
        toSchedule.add(t);
      }

      // Cancel ONLY stale reminders — a pending id inside SOME live task's
      // reserved range that's no longer wanted (completed / offsets shrunk /
      // time removed). We must NOT blanket-cancel "every pending id not in
      // desired": that would also nuke the Settings "Schedule test reminder" (a
      // non-task id) and any reminder a concurrent addTask just scheduled for a
      // task not yet present in this (possibly stale) list.
      final known = <int>{for (final t in tasks) ..._reservedIds(t.id)};
      final pending = await _sink.pendingIds();
      for (final id in pending) {
        if (known.contains(id) && !desired.contains(id)) {
          await _sink.cancel(id);
        }
      }

      for (final t in toSchedule) {
        await scheduleForTask(t);
      }
    } catch (_) {
      // Best-effort.
    }
  }
}
