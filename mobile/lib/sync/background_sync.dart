import 'package:flutter/widgets.dart';
import 'package:workmanager/workmanager.dart';

import '../core/api/api_client.dart';
import '../core/config/server_config.dart';
import '../core/home_widget_tasks.dart';
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
    } catch (_) {
      // Returning false asks the OS to retry with backoff.
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
    } catch (_) {
      // Task sync failure is non-fatal — continue with remaining domains.
    }

    // Repaint the home-screen Tasks widget off the freshly synced cache.
    // Without this the widget only updated while the APP was open — tasks
    // created via Telegram/the agent never reached the glanceable view.
    // [updateTasksWidget] is internally guarded and never throws.
    try {
      await updateTasksWidget(await TaskDao(db).list());
    } catch (_) {
      // Widget repaint is non-fatal.
    }

    // Notes
    try {
      await NoteSync(
        NoteDao(db),
        NotesRepository(DioNotesTransport(client)),
      ).sync();
    } catch (_) {
      // Note sync failure is non-fatal — continue with remaining domains.
    }

    // Budgets (projects + expenses share one cursor)
    try {
      await BudgetsSync(
        BudgetsDao(db),
        BudgetsRepository(DioBudgetsTransport(client)),
      ).sync();
    } catch (_) {
      // Budgets sync failure is non-fatal.
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
        } catch (_) {
          // One kind failing must not abort the others.
        }
      }
    } catch (_) {
      // Document sync failure is non-fatal.
    }

    // Server-notification feed: surface watcher/background-job/escalation
    // notifications missed while the app was closed. We are in a fresh isolate
    // so the plugin must be (re)initialised before it can show anything.
    // [pullNotificationsFeed] swallows its own errors.
    try {
      await LocalNotifications.init();
      await pullNotificationsFeed(client);
    } catch (_) {
      // Notification catch-up is non-fatal.
    }
  } finally {
    await db.close();
  }
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
  } catch (_) {
    // Best-effort: foreground sync (on load / refresh / reachability flip)
    // still keeps the cache fresh even if background scheduling is unavailable.
  }
}
