import 'package:flutter/widgets.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

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
    final db = opened.db;
    final result = await TaskDao(db).applyLocalComplete(taskId);
    return result != null;
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
