import 'package:flutter/widgets.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

import '../core/notifications/task_reminder_service.dart';
import '../local/app_db.dart';
import '../local/task_dao.dart';

/// The `actionId` of the "Done" button attached to task-reminder notifications.
/// Shared by the foreground response handler ([LocalNotifications]) and the
/// background isolate entry point ([notificationBackgroundHandler]) so the two
/// paths agree on what a "complete this task" tap looks like.
const String kTaskDoneActionId = 'task_done';

/// Prefix of the payload a task reminder carries: `task:<id>`. The reminder is
/// scheduled with `payload: 'task:${task.id}'`, so a "Done" tap delivers it
/// back to us and we can recover the task id.
const String kTaskPayloadPrefix = 'task:';

/// Parse a task id out of a notification [payload], or null when the payload is
/// not a `task:<id>` reference (e.g. a `'chat'` server-notification payload, an
/// empty/whitespace id, or null).
///
/// PURE + side-effect-free so it can be unit-tested without a plugin, a DB, or
/// an isolate. Trims surrounding whitespace and treats a blank id as absent.
String? taskIdFromPayload(String? payload) {
  if (payload == null) return null;
  final trimmed = payload.trim();
  if (!trimmed.startsWith(kTaskPayloadPrefix)) return null;
  final id = trimmed.substring(kTaskPayloadPrefix.length).trim();
  return id.isEmpty ? null : id;
}

/// Whether [response] is a tap on the task-reminder "Done" action button (as
/// opposed to a plain body tap or some other action). PURE — just inspects the
/// `actionId`.
bool isTaskDoneAction(NotificationResponse response) =>
    response.actionId == kTaskDoneActionId;

/// Called (on whatever isolate handled the tap) right after a task was completed
/// from the notification shade, with the completed task's id.
///
/// WHY it exists: the shade "Done" path writes straight to SQLite, bypassing the
/// tasks notifier — so an OPEN Tasks list kept showing the task as pending until
/// the next manual refresh. The app shell assigns this hook so it can invalidate
/// / refresh the list. Null (and therefore a no-op) in the background isolate,
/// where there is no UI and no Riverpod scope to notify.
void Function(String taskId)? onTaskCompletedFromNotification;

/// Cancel EVERY local alarm reserved for [taskId].
///
/// The shade "Done" button has `cancelNotification: true`, which dismisses only
/// the notification that was TAPPED. A task schedules one alarm per configured
/// offset (`-2h`, `-1h`, at-time…), so completing from the -2h reminder used to
/// leave the later siblings armed and the user got reminded again about a task
/// they had already finished. Sweeping [reservedReminderIds] is the same range
/// [TaskReminderService.cancelForTask] clears.
///
/// [sink] is injectable for tests; production wires the shared plugin (its
/// unnamed constructor returns the process-wide singleton, i.e. the very same
/// instance `LocalNotifications.plugin` exposes). Best-effort — never throws.
Future<void> cancelTaskReminderAlarms(String taskId, {ReminderSink? sink}) async {
  try {
    final target =
        sink ?? FlutterLocalNotificationsSink(FlutterLocalNotificationsPlugin());
    for (final id in reservedReminderIds(taskId)) {
      await target.cancel(id);
    }
  } catch (_) {
    // A cancel failure must never break the completion itself.
  }
}

/// Complete the task referenced by [payload] (`task:<id>`) against an ALREADY-OPEN
/// [dao], cancel the task's whole reminder alarm set, and notify
/// [onTaskCompletedFromNotification]. Returns true iff a row was completed.
///
/// Split out of [completeTaskFromPayload] so the behaviour is unit-testable
/// against a real in-memory SQLite engine — the public entry point below can't
/// be, because it opens the encrypted on-device DB through platform channels.
Future<bool> completeTaskInDao(
  TaskDao dao,
  String? payload, {
  ReminderSink? sink,
}) async {
  final taskId = taskIdFromPayload(payload);
  if (taskId == null) return false;
  final result = await dao.applyLocalComplete(taskId);
  if (result == null) return false;
  // Order matters only for clarity: the row is already done, so a failure in
  // either best-effort step below still leaves a correctly completed task.
  await cancelTaskReminderAlarms(taskId, sink: sink);
  try {
    onTaskCompletedFromNotification?.call(taskId);
  } catch (_) {
    // A listener error must never fail the completion.
  }
  return true;
}

/// Complete the task referenced by [payload] (`task:<id>`) directly against the
/// encrypted on-device database, WITHOUT any Riverpod scope or running app.
///
/// This is the SAME local mutation the in-app "complete" performs
/// ([TaskDao.applyLocalComplete]): it sets `status = done` + `completed_at` +
/// `updated_at = now`, marks the row dirty, and ENQUEUES a `complete` op onto
/// the OUTBOX so the next sync pass (foreground resume or the periodic
/// WorkManager job) pushes it to the server. Last-write-wins by `updated_at`
/// then reconciles with any concurrent server change.
///
/// Mirrors the headless DB-open pattern in `sync/background_sync.dart`: it opens
/// the SQLCipher DB (key from secure storage) in whatever isolate it is called
/// from, applies the mutation, and always closes the handle. BEST-EFFORT — it
/// never throws, so a missing task, a locked/corrupt DB, or a denied keychain
/// simply means the tap is a no-op rather than crashing the (head-less) isolate.
///
/// Returns true iff a matching task row was found and marked complete.
Future<bool> completeTaskFromPayload(String? payload) async {
  final taskId = taskIdFromPayload(payload);
  if (taskId == null) return false;

  // The plugin invokes the background callback in a fresh isolate with no
  // Flutter binding; opening the DB pulls in path_provider + secure_storage,
  // both of which need the platform channels initialised.
  WidgetsFlutterBinding.ensureInitialized();

  // Resilient open: a transient lock (the foreground app or the sync isolate
  // briefly holds the file) is retried rather than degraded to a throwaway
  // in-memory DB — completing into an ephemeral DB would lose the mutation.
  //
  // DEDICATED connection (singleInstance: false): this runs in a background
  // isolate on a killed-app action tap, but ALSO in the MAIN isolate on a
  // foreground "Done" tap (LocalNotifications._onResponse). With sqflite's
  // default singleInstance: true it would receive the very same handle as the
  // foreground appDatabaseProvider connection (handles are keyed by PATH), and
  // the close() in the finally below would kill the app's DB out from under it
  // (DatabaseException(database_closed)). A dedicated handle is safe to close.
  AppDbResult? opened;
  try {
    opened = await openAppDbWithFallback(singleInstance: false);
    // Completing ALSO cancels the task's sibling offset alarms and pings the
    // refresh hook — see [completeTaskInDao].
    return await completeTaskInDao(TaskDao(opened.db), payload);
  } catch (_) {
    // Never let a completion failure crash the notification isolate.
    return false;
  } finally {
    try {
      await opened?.db.close();
    } catch (_) {
      // Closing a degraded/half-open handle can throw — ignore.
    }
  }
}

/// TOP-LEVEL background entry point for notification ACTION taps that arrive
/// while the app is backgrounded OR fully killed. Wired into
/// `FlutterLocalNotificationsPlugin.initialize` as
/// `onDidReceiveBackgroundNotificationResponse`.
///
/// Must be a top-level (or static) function annotated `@pragma('vm:entry-point')`
/// so the AOT compiler retains it and the plugin's `ActionBroadcastReceiver` can
/// spin up a background isolate that calls it. Only the "Done" action is handled
/// here; a plain body tap in the background is left to the OS (it relaunches the
/// app, and `consumeLaunchPayload` deep-links on the next cold start).
@pragma('vm:entry-point')
void notificationBackgroundHandler(NotificationResponse response) {
  if (!isTaskDoneAction(response)) return;
  // Fire-and-forget: the isolate stays alive until this future settles because
  // the plugin awaits the callback. completeTaskFromPayload swallows its own
  // errors, so this never throws out of the entry point.
  completeTaskFromPayload(response.payload);
}
