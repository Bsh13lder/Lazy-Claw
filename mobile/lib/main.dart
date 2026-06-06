import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'core/config/server_config.dart';
import 'core/router/app_router.dart';
import 'core/self_update.dart';
import 'local/app_db.dart';
import 'ui/app_theme.dart';
import 'providers/auth_provider.dart';
import 'providers/budgets_provider.dart';
import 'providers/notes_provider.dart';
import 'providers/tasks_provider.dart';
import 'sync/background_sync.dart';
import 'sync/foreground_sync.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final baseUrl = await ServerConfig.load();

  // Open the encrypted offline DB up front so the Tasks tab is instant and
  // works with the backend unreachable. The resilient path retries the file DB
  // then falls back to an ephemeral in-memory DB — it ALWAYS returns a usable
  // handle, so the provider graph can never crash on a DB-open failure. The
  // resulting [DbHealth] drives the degraded-mode banner in the UI.
  final result = await openAppDbWithFallback();

  // Best-effort periodic background sync (~30 min). Never blocks startup.
  await registerBackgroundSync();

  runApp(ProviderScope(
    overrides: [
      baseUrlProvider.overrideWith((ref) => baseUrl),
      appDatabaseProvider.overrideWithValue(result.db),
      dbHealthProvider.overrideWith((ref) => result.health),
    ],
    child: const LazyClawApp(),
  ));
}

class LazyClawApp extends ConsumerStatefulWidget {
  const LazyClawApp({super.key});
  @override
  ConsumerState<LazyClawApp> createState() => _LazyClawAppState();
}

class _LazyClawAppState extends ConsumerState<LazyClawApp> {
  ForegroundSyncScheduler? _fgSync;

  @override
  void initState() {
    super.initState();
    Future.microtask(() => ref.read(authProvider.notifier).checkSession());

    // Keep offline-first data fresh while the app is in the foreground: every
    // ~30 min (and on each resume) push/pull all three offline-first domains.
    _fgSync = ForegroundSyncScheduler(onSync: () async {
      await ref.read(tasksProvider.notifier).syncNow();
      await ref.read(notesProvider.notifier).syncNow();
      await ref.read(budgetsProvider.notifier).syncNow();
    });
    _fgSync!.start();

    // Non-blocking startup self-update check. `checkForUpdate` never throws
    // (returns null on any failure), so this can run fire-and-forget.
    Future.microtask(() async {
      final info = await ref.read(selfUpdateServiceProvider).checkForUpdate();
      if (info != null && mounted) {
        ref.read(updateAvailableProvider.notifier).state = info;
      }
    });
  }

  @override
  void dispose() {
    _fgSync?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final router = ref.watch(routerProvider);
    return MaterialApp.router(
      title: 'LazyClaw',
      theme: buildAppTheme(),
      routerConfig: router,
    );
  }
}
