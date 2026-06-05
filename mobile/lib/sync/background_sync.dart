import 'package:flutter/widgets.dart';
import 'package:workmanager/workmanager.dart';

import '../core/api/api_client.dart';
import '../core/config/server_config.dart';
import '../local/app_db.dart';
import '../local/task_dao.dart';
import '../repositories/tasks_repository.dart';
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
      await runHeadlessTaskSync();
      return true;
    } catch (_) {
      // Returning false asks the OS to retry with backoff.
      return false;
    }
  });
}

/// Open the DB + build a sync engine from scratch (we are in a fresh isolate
/// with no Riverpod scope) and run one sync pass. Safe to call when offline —
/// the engine simply no-ops the network parts.
Future<void> runHeadlessTaskSync() async {
  WidgetsFlutterBinding.ensureInitialized();
  final baseUrl = await ServerConfig.load();
  final db = await openAppDb();
  try {
    final dao = TaskDao(db);
    final repo = TasksRepository(DioTasksTransport(ApiClient(baseUrl: baseUrl)));
    await TaskSync(dao, repo).sync();
  } finally {
    await db.close();
  }
}

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
