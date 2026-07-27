import 'package:flutter/widgets.dart';
import 'package:workmanager/workmanager.dart';

import '../core/api/api_client.dart';
import '../core/config/server_config.dart';
import '../core/home_widget_tasks.dart';
import '../core/notifications/task_reminder_service.dart';
import '../core/reminder_offset.dart';
import '../local/app_db.dart';
import '../local/budgets_dao.dart';
import '../local/document_cache_dao.dart';
import '../local/note_dao.dart';
import '../local/task_dao.dart';
import '../notifications/local_notifications.dart';
import '../notifications/notifications_service.dart';
import '../repositories/budgets_repository.dart';
import '../repositories/documents_repository.dart';
import '../repositories/notes_repository.dart';
import '../repositories/tasks_repository.dart';
import '../screens/settings/settings_prefs.dart';
import 'budgets_sync.dart';
import 'document_sync.dart';
import 'note_sync.dart';
import 'task_sync.dart';

/// Unique name for the periodic task-sync job.
const String kTaskSyncTaskName = 'lazyclaw.task_sync.periodic';
const String kTaskSyncUniqueName = 'lazyclaw_task_sync';

/// How often the OS wakes us to drain the outbox + pull deltas (best-effort;
/// the platform may coalesce/delay this). 15 min is the Android floor; we ask
/// for ~30 min.
const Duration kTaskSyncInterval = Duration(minutes: 30);

/// Entry point invoked by workmanager in a BACKGROUND isolate. Must be a
/// top-level / static function annotated for AOT retention.
@pragma('vm:entry-point')
void backgroundSyncDispatcher() {
  Workmanager().executeTask((taskName, inputData) async {
    if (taskName != kTaskSyncTaskName) return true;
    try {
      await runHeadlessSync();
      return true;
    } catch (e) {
      // Returning false asks the OS to retry with backoff.
      debugPrint(
        'backgroundSyncDispatcher: headless sync failed — asking OS to retry '
        'with backoff: $e',
      );
      return false;
    }
  });
}

/// Open the DB + build all three sync engines from scratch (we are in a fresh
/// isolate with no Riverpod scope) and run one sync pass for Tasks, Notes, and
/// Budgets. Safe to call when offline — each engine simply no-ops the network
/// parts. Each domain is wrapped in its own try/catch so one failing domain
/// does not abort the others. The DB is opened once and shared across all three.
Future<void> runHeadlessSync() async {
  WidgetsFlutterBinding.ensureInitialized();
  // Resolve a REACHABLE gateway (honoring any manual override) instead of the
  // hardwired remote URL — the DuckDNS front door can't be reached on home WiFi
  // (no NAT hairpin), so the old `ServerConfig.load()` meant background sync
  // could NEVER run there. probing self-heals to the LAN host in that case.
  final baseUrl = await ServerConfig.resolveBaseUrl();
  debugPrint('runHeadlessSync: starting headless sync against $baseUrl');
  final client = ApiClient(baseUrl: baseUrl);
  // DEDICATED connection (singleInstance: false): with sqflite's default
  // singleInstance: true, native handles are keyed by PATH — this background
  // isolate would receive the SAME native handle as the foreground app's
  // appDatabaseProvider connection, and the close() in the finally below would
  // kill the app's DB out from under it (DatabaseException(database_closed) on
  // the next foreground query). A dedicated handle is safe to close; WAL +
  // busy_timeout (configureAppDb) already make the two connections coexist.
  final db = await openAppDb(singleInstance: false);
  try {
    // Tasks
    try {
      await TaskSync(
        TaskDao(db),
        TasksRepository(DioTasksTransport(client)),
      ).sync();
    } catch (e) {
      // Task sync failure is non-fatal — continue with remaining domains.
      debugPrint('runHeadlessSync: task sync failed (non-fatal): $e');
    }

    // Repaint the home-screen Tasks widget off the freshly synced cache.
    // Without this the widget only updated while the APP was open — tasks
    // created via Telegram/the agent never reached the glanceable view.
    // [updateTasksWidget] is internally guarded and never throws.
    try {
      await updateTasksWidget(await TaskDao(db).list());
    } catch (e) {
      // Widget repaint is non-fatal.
      debugPrint('runHeadlessSync: tasks widget repaint failed (non-fatal): $e');
    }

    // Notes
    try {
      await NoteSync(
        NoteDao(db),
        NotesRepository(DioNotesTransport(client)),
      ).sync();
    } catch (e) {
      // Note sync failure is non-fatal — continue with remaining domains.
      debugPrint('runHeadlessSync: note sync failed (non-fatal): $e');
    }

    // Budgets (projects + expenses share one cursor)
    try {
      await BudgetsSync(
        BudgetsDao(db),
        BudgetsRepository(DioBudgetsTransport(client)),
      ).sync();
    } catch (e) {
      // Budgets sync failure is non-fatal.
      debugPrint('runHeadlessSync: budgets sync failed (non-fatal): $e');
    }

    // Documents (Sheets/Docs/PDF): drain outbox + pull /changes for each kind so
    // a delete/edit made on web or by the agent propagates to mobile, and any
    // local-first edit made offline pushes. One DAO + repo shared across kinds.
    try {
      final docDao = DocumentCacheDao(db);
      final docRepo = DocumentsRepository(DioDocumentsTransport(client));
      for (final kind in DocKind.values) {
        try {
          await DocumentSync(docDao, docRepo, kind).sync();
        } catch (e) {
          // One kind failing must not abort the others.
          debugPrint(
            'runHeadlessSync: document sync failed for kind=${kind.api} '
            '(non-fatal): $e',
          );
        }
      }
    } catch (e) {
      // Document sync failure is non-fatal.
      debugPrint('runHeadlessSync: document sync setup failed (non-fatal): $e');
    }

    // Server-notification feed: surface watcher/background-job/escalation
    // notifications missed while the app was closed. We are in a fresh isolate
    // so the plugin must be (re)initialised before it can show anything.
    // [pullNotificationsFeed] swallows its own errors.
    try {
      await LocalNotifications.init();
      await pullNotificationsFeed(client);
    } catch (e) {
      // Notification catch-up is non-fatal.
      debugPrint('runHeadlessSync: notification catch-up failed (non-fatal): $e');
    }

    // Re-arm the on-device reminder alarms for whatever the pull just brought
    // in. WITHOUT this, a phone that is never cold-started got NO reminders for
    // any task it learned about in the background — most visibly every RECURRING
    // respawn (the backend completes occurrence N and writes N+1 server-side, so
    // the new occurrence only ever reaches us through a sync). Reconcile (not
    // "schedule the new ones") so alarms for tasks completed/retimed elsewhere
    // are swept in the same pass. Internally guarded — never throws.
    //
    // Runs LAST deliberately: syncAll issues kMaxOffsetReminders cancel
    // round-trips PER TASK over the platform channel plus one zonedSchedule per
    // planned fire, so on a large cache it is by far the most expensive step.
    // Ahead of the other domains it could eat the WorkManager budget and starve
    // Notes/Budgets/Documents/the notification feed; at the end, a slow reconcile
    // only ever costs itself. It also means it reconciles against the FULLY
    // synced cache rather than a half-updated one.
    await reconcileRemindersHeadless(TaskDao(db));
  } finally {
    await db.close();
    debugPrint('runHeadlessSync: headless sync pass complete');
  }
}

/// Reconcile the LOCAL reminder alarms against the task cache in [dao], from a
/// headless isolate (no Riverpod scope, so [taskReminderServiceProvider] is
/// unavailable).
///
/// Reads the whole cache and hands it to [TaskReminderScheduler.syncAll], which
/// cancels stale alarms and (re)schedules the live ones — so a respawned
/// recurring occurrence pulled while the app was closed gets its alarms armed,
/// and a task completed on another device gets its alarms dropped.
///
/// [scheduler] is injectable for tests; production builds one via
/// [buildHeadlessReminderScheduler]. BEST-EFFORT: every failure (plugin absent
/// in this isolate, denied permission, unreadable prefs) is swallowed and logged
/// so it can never abort the surrounding sync pass.
Future<void> reconcileRemindersHeadless(
  TaskDao dao, {
  TaskReminderScheduler? scheduler,
}) async {
  try {
    final tasks = await dao.list();
    final target = scheduler ?? await buildHeadlessReminderScheduler();
    await target.syncAll(tasks);
  } catch (e) {
    debugPrint(
      'reconcileRemindersHeadless: could not re-arm reminders (non-fatal): $e',
    );
  }
}

/// Build a [TaskReminderService] usable inside a background isolate.
///
/// Two things the foreground provider gets for free must be done by hand here:
///   * the notification plugin + IANA timezone db are per-isolate, so
///     [LocalNotifications.init] must run before `zonedSchedule` can be called
///     (idempotent — the later notification-feed catch-up reuses it);
///   * the user's reminder prefs come from secure storage directly rather than
///     `settingsPrefsProvider`. An unreadable keystore falls back to the same
///     defaults the provider uses, so we still schedule *something* sane.
Future<TaskReminderScheduler> buildHeadlessReminderScheduler() async {
  await LocalNotifications.init();
  var minutes = kDefaultReminderMinutes;
  var offsets = List<String>.of(kDefaultReminderOffsets);
  try {
    minutes = await SettingsPrefs.loadDefaultReminderMinutes();
    offsets = await SettingsPrefs.loadReminderOffsets();
  } catch (e) {
    // Prefs unreadable in this isolate — schedule with the shipped defaults
    // rather than not scheduling at all.
    debugPrint(
      'buildHeadlessReminderScheduler: reminder prefs unreadable, using '
      'defaults (non-fatal): $e',
    );
  }
  return TaskReminderService(
    FlutterLocalNotificationsSink(LocalNotifications.plugin),
    defaultReminderMinutes: minutes,
    offsets: offsets,
  );
}

/// Kept for backward compatibility — delegates to [runHeadlessSync].
@Deprecated('Use runHeadlessSync() — it now covers Tasks, Notes, and Budgets.')
Future<void> runHeadlessTaskSync() => runHeadlessSync();

/// Register the periodic background sync. Wrapped so a platform that lacks
/// WorkManager support (or denies it) never crashes app startup.
Future<void> registerBackgroundSync() async {
  try {
    await Workmanager().initialize(backgroundSyncDispatcher);
    await Workmanager().registerPeriodicTask(
      kTaskSyncUniqueName,
      kTaskSyncTaskName,
      frequency: kTaskSyncInterval,
      existingWorkPolicy: ExistingWorkPolicy.keep,
      constraints: Constraints(networkType: NetworkType.connected),
    );
  } catch (e) {
    // Best-effort: foreground sync (on load / refresh / reachability flip)
    // still keeps the cache fresh even if background scheduling is unavailable.
    debugPrint(
      'registerBackgroundSync: WorkManager registration unavailable — relying '
      'on foreground sync only: $e',
    );
  }
}
