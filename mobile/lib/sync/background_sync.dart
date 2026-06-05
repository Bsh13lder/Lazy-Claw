import 'package:flutter/widgets.dart';
import 'package:workmanager/workmanager.dart';

import '../core/api/api_client.dart';
import '../core/config/server_config.dart';
import '../local/app_db.dart';
import '../local/budgets_dao.dart';
import '../local/note_dao.dart';
import '../local/task_dao.dart';
import '../repositories/budgets_repository.dart';
import '../repositories/notes_repository.dart';
import '../repositories/tasks_repository.dart';
import 'budgets_sync.dart';
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
  final baseUrl = await ServerConfig.load();
  final client = ApiClient(baseUrl: baseUrl);
  final db = await openAppDb();
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
