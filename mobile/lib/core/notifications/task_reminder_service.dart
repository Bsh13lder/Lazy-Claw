import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:timezone/timezone.dart' as tz;

import '../../models/task.dart';
import '../../notifications/notification_actions.dart';
import '../due_date.dart';

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

// ── Production implementation ────────────────────────────────────────────────

/// Schedules task reminders via the shared [FlutterLocalNotificationsPlugin].
///
/// Reuses the app's single plugin instance (pass `LocalNotifications.plugin`).
/// Every method is best-effort and never throws — a denied permission, an
/// uninitialised timezone db, or a missing plugin simply means no reminder.
class TaskReminderService implements TaskReminderScheduler {
  TaskReminderService(this._plugin, {int defaultReminderMinutes = 540})
      : _defaultReminderMinutes = _clampMinutes(defaultReminderMinutes);

  final FlutterLocalNotificationsPlugin _plugin;

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

  /// Keep the configured time-of-day inside a valid day (0..1439). An absurd
  /// value can't push the fallback onto another calendar day.
  static int _clampMinutes(int minutes) =>
      minutes < 0 ? 0 : (minutes > 1439 ? 1439 : minutes);

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
  Future<void> scheduleForTask(Task task) async {
    final id = notificationIdForTask(task.id);
    try {
      // Always clear any prior schedule first, so an edit that moves OR removes
      // the time can't leave a stale alarm behind.
      await _plugin.cancel(id);
      if (task.isDone) return;
      final fire = reminderFireTime(
        task,
        defaultReminderHour: _defaultHour,
        defaultReminderMinute: _defaultMinute,
      );
      if (fire == null) return;

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
      final title = task.title.isEmpty ? 'Task reminder' : task.title;
      final body = _bodyFor(task, fire);
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
          payload: 'task:${task.id}',
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
          payload: 'task:${task.id}',
        );
      }
    } catch (_) {
      // Best-effort: never let a scheduling failure break the task write.
    }
  }

  @override
  Future<void> cancelForTask(String taskId) async {
    try {
      await _plugin.cancel(notificationIdForTask(taskId));
    } catch (_) {
      // Best-effort.
    }
  }

  @override
  Future<void> syncAll(List<Task> tasks) async {
    try {
      final desired = <int, Task>{};
      for (final t in tasks) {
        if (t.isDone) continue;
        if (reminderFireTime(
              t,
              defaultReminderHour: _defaultHour,
              defaultReminderMinute: _defaultMinute,
            ) ==
            null) {
          continue;
        }
        desired[notificationIdForTask(t.id)] = t;
      }

      // Cancel ONLY stale TASK reminders — ids that are the FNV id of a task in
      // [tasks] but are no longer wanted (completed / time removed). We must NOT
      // blanket-cancel "every pending id not in desired": that would also nuke
      // the Settings "Schedule test reminder" (a zonedSchedule with a non-task
      // id) and any reminder a concurrent addTask just scheduled for a task not
      // yet present in this (possibly stale) list.
      final knownTaskIds = {for (final t in tasks) notificationIdForTask(t.id)};
      List<PendingNotificationRequest> pending;
      try {
        pending = await _plugin.pendingNotificationRequests();
      } catch (_) {
        pending = const [];
      }
      for (final req in pending) {
        if (knownTaskIds.contains(req.id) && !desired.containsKey(req.id)) {
          await _plugin.cancel(req.id);
        }
      }

      for (final t in desired.values) {
        await scheduleForTask(t);
      }
    } catch (_) {
      // Best-effort.
    }
  }

  String _bodyFor(Task task, DateTime fire) {
    final label = formatClock12(fire.hour, fire.minute);
    final reminderDriven =
        task.reminderAt != null && task.reminderAt!.isNotEmpty;
    if (reminderDriven) return 'Reminder · $label';
    // A date-only due fires at the default time-of-day, but the task isn't
    // literally "due at" that clock time — phrase it as a day reminder so the
    // notification text doesn't claim a due time the task never had.
    if (!dueDateHasTime(task.dueDate)) return 'Due today · reminder';
    return 'Due at $label';
  }
}
