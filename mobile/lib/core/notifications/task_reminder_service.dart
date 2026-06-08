import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:timezone/timezone.dart' as tz;

import '../../models/task.dart';
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
/// Precedence:
///   1. An explicit [Task.reminderAt] is authoritative when present.
///   2. Otherwise the task's [Task.dueDate] is used ONLY when it carries a
///      time-of-day (`…THH:mm:ss`). A bare date-only due (`yyyy-MM-dd`)
///      schedules nothing — we don't invent a time-of-day.
///
/// Returns null when the chosen instant is in the past or exactly [now] (the
/// moment already passed, so there's nothing to fire) or when no valid instant
/// exists. `reminderAt`, being explicit, is authoritative: if it's set but
/// already passed we schedule nothing — we do NOT silently fall back to the
/// due time.
DateTime? reminderFireTime(Task task, {DateTime? now}) {
  final clock = now ?? DateTime.now();
  final candidate =
      _parseReminderAt(task.reminderAt) ?? _parseDueWithTime(task.dueDate);
  if (candidate == null) return null;
  if (!candidate.isAfter(clock)) return null;
  return candidate;
}

DateTime? _parseReminderAt(String? reminderAt) {
  if (reminderAt == null || reminderAt.isEmpty) return null;
  return DateTime.tryParse(reminderAt);
}

DateTime? _parseDueWithTime(String? due) {
  if (!dueDateHasTime(due)) return null;
  return DateTime.tryParse(due!);
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
  TaskReminderService(this._plugin);

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
      final fire = reminderFireTime(task);
      if (fire == null) return;

      // tz.TZDateTime.from throws if the timezone db / local location is not
      // initialised — the outer catch turns that into a silent no-op rather
      // than crashing a write path.
      final when = tz.TZDateTime.from(fire, tz.local);
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
        if (reminderFireTime(t) == null) continue;
        desired[notificationIdForTask(t.id)] = t;
      }

      // Cancel stale scheduled reminders (tasks deleted / completed / had their
      // time removed). The only notifications this app ever *schedules* are
      // task reminders, so any pending request not in [desired] is ours to
      // clear. (Chat/approval notifications use show(), not zonedSchedule, so
      // they never appear in pendingNotificationRequests.)
      List<PendingNotificationRequest> pending;
      try {
        pending = await _plugin.pendingNotificationRequests();
      } catch (_) {
        pending = const [];
      }
      for (final req in pending) {
        if (!desired.containsKey(req.id)) {
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
    return reminderDriven ? 'Reminder · $label' : 'Due at $label';
  }
}
