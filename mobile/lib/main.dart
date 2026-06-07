import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'core/actions/app_actions.dart';
import 'core/actions/deep_link_service.dart';
import 'core/config/server_config.dart';
import 'core/router/app_router.dart';
import 'core/self_update.dart';
import 'local/app_db.dart';
import 'notifications/local_notifications.dart';
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

  // Initialise local notifications + the timezone db up front so scheduled
  // task reminders can be (re)scheduled at startup (see the syncAll below) and
  // so zonedSchedule has a valid local location. Never throws.
  await LocalNotifications.init();

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
  DeepLinkService? _deepLinks;

  @override
  void initState() {
    super.initState();
    Future.microtask(() => ref.read(authProvider.notifier).checkSession());

    // (Re)schedule local reminders for every cached task at app start, so
    // due/reminder times survive an app restart and reboot even if the user
    // never opens the Tasks tab. Reads straight from the encrypted local cache
    // (no network). Best-effort — never throws, never blocks the UI.
    Future.microtask(() async {
      try {
        final tasks = await ref.read(taskDaoProvider).list();
        await ref.read(taskReminderServiceProvider).syncAll(tasks);
      } catch (_) {
        // Notifications are non-critical; a failure here must not affect boot.
      }
    });

    // Home-screen access (Phase 5): wire the launcher long-press shortcuts and
    // the home-screen Quick-Capture widget into [pendingActionProvider]. Done
    // after the first frame so the router/provider graph exists; cold-start
    // triggers (app launched BY a shortcut/widget) are stashed and replayed by
    // the per-screen consumers + the navigation listener in [build].
    _deepLinks = DeepLinkService((action) {
      if (!mounted) return;
      ref.read(pendingActionProvider.notifier).state = action;
    });
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _deepLinks?.init();
    });

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
    _deepLinks?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final router = ref.watch(routerProvider);

    // Navigate to the right branch when a deep-link action is pending. The
    // destination screen (Tasks / Expenses) consumes the action to open its
    // sheet; `chat` has no sheet, so it's consumed here right after navigating.
    ref.listen<AppAction?>(pendingActionProvider, (_, next) {
      if (next == null) return;
      router.go(routeForAction(next));
      if (next == AppAction.chat) {
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (mounted) ref.read(pendingActionProvider.notifier).state = null;
        });
      }
    });

    return MaterialApp.router(
      title: 'LazyClaw',
      theme: buildAppTheme(),
      routerConfig: router,
    );
  }
}
