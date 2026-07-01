import 'dart:ui' show PlatformDispatcher;

import 'package:flutter/material.dart';
import 'package:flutter_quill/flutter_quill.dart' show FlutterQuillLocalizations;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'core/actions/app_actions.dart';
import 'wake/native_wake_service.dart';
import 'core/actions/deep_link_service.dart';
import 'core/crash_log.dart';
import 'core/config/server_config.dart';
import 'core/router/app_router.dart';
import 'core/self_update.dart';
import 'local/app_db.dart';
import 'notifications/local_notifications.dart';
import 'notifications/notifications_service.dart';
import 'ui/app_theme.dart';
import 'providers/auth_provider.dart';
import 'providers/budgets_provider.dart';
import 'providers/documents_provider.dart';
import 'providers/notes_provider.dart';
import 'providers/tasks_provider.dart';
import 'sync/background_sync.dart';
import 'sync/foreground_sync.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Capture uncaught Dart errors so a "random crash" leaves a trace the user can
  // read + share (Settings → Recent errors) instead of vanishing — AND so an
  // uncaught async error doesn't hard-crash the whole app. (Native plugin
  // crashes / OS force-kills bypass these handlers and need `adb logcat`.)
  FlutterError.onError = (details) {
    FlutterError.presentError(details);
    CrashLog.record(details.exception, details.stack);
  };
  PlatformDispatcher.instance.onError = (error, stack) {
    CrashLog.record(error, stack);
    return true; // handled — keep the app alive
  };

  // Replace Flutter's blank/black release error placeholder with a readable,
  // screenshot-able dump (exception + stack) so a build-time crash is VISIBLE
  // on-screen instead of a black screen. The error is also recorded by
  // FlutterError.onError above (Settings → Recent errors). Native crashes
  // (e.g. a .so SIGSEGV) still bypass this and need `adb logcat`.
  ErrorWidget.builder = (FlutterErrorDetails details) {
    return Directionality(
      textDirection: TextDirection.ltr,
      child: Container(
        color: const Color(0xFF0d0d0d),
        alignment: Alignment.topLeft,
        padding: const EdgeInsets.fromLTRB(16, 64, 16, 16),
        child: SingleChildScrollView(
          child: Text(
            'LazyClaw hit an error — please screenshot this:\n\n'
            '${details.exceptionAsString()}\n\n'
            '${details.stack ?? ''}',
            style: const TextStyle(
                color: Color(0xFFff9090), fontSize: 12, height: 1.3),
          ),
        ),
      ),
    );
  };

  // The app is locked to a single gateway (the DuckDNS + Caddy front door) so
  // the session cookie + auth cache stay consistent on WiFi and cellular alike.
  final baseUrl = await ServerConfig.resolveBaseUrl();

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

    // Re-arm the "Hey Lazy" wake word on launch if the user left it on. Android
    // 14/15 block silent boot resume of a mic foreground service, so we re-arm
    // here (reading the provider triggers its restoreAndRearm) instead of from a
    // boot receiver.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) ref.read(wakeEnabledProvider);
    });

    // Deep-link a tapped server notification into the Chat tab. Reuses the
    // pending-action plumbing: the listener in [build] navigates + clears it.
    // The hook is set here (after init) so it has provider access; it is read
    // at call time, so binding it after [LocalNotifications.init] is fine.
    LocalNotifications.onSelectNotification = (_) {
      if (!mounted) return;
      ref.read(pendingActionProvider.notifier).state = AppAction.chat;
    };
    // Cold-start: app launched BY tapping a server notification.
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      final payload = await LocalNotifications.consumeLaunchPayload();
      if (payload != null && mounted) {
        ref.read(pendingActionProvider.notifier).state = AppAction.chat;
      }
    });

    // Keep offline-first data fresh while the app is in the foreground: every
    // ~30 min (and on each resume) push/pull all three offline-first domains.
    _fgSync = ForegroundSyncScheduler(onSync: () async {
      await ref.read(tasksProvider.notifier).syncNow();
      await ref.read(notesProvider.notifier).syncNow();
      await ref.read(budgetsProvider.notifier).syncNow();
      // Documents (Sheets/Docs/PDF): drain outbox + pull /changes so web-created
      // / edited docs surface on mobile and local-first edits push.
      await syncAllDocuments(ref.read);
      // Catch up on server notifications on each resume + ~30-min tick.
      await pullNotificationsFeed(ref.read(apiClientProvider));
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

  /// Navigate to the branch a pending [action] belongs to. The destination
  /// screen (Tasks / Expenses) then DRAINS the action to open its sheet.
  /// `chat`, `openTasks` and `assistant` have NO sheet (they just land on a
  /// destination), so they're consumed here right after navigating — no screen
  /// drains them. `/assistant` is a full-screen route OUTSIDE the shell, so we
  /// `push` it (preserving the underlying tab) rather than `go`.
  void _navigateForAction(GoRouter router, AppAction action) {
    if (action == AppAction.assistant) {
      router.push(routeForAction(action));
    } else {
      router.go(routeForAction(action));
    }
    if (action == AppAction.chat ||
        action == AppAction.openTasks ||
        action == AppAction.assistant) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) ref.read(pendingActionProvider.notifier).state = null;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final router = ref.watch(routerProvider);

    // Navigate to the right branch when a deep-link action is pending.
    ref.listen<AppAction?>(pendingActionProvider, (_, next) {
      if (next == null) return;
      _navigateForAction(router, next);
    });

    // COLD-START SAFETY NET: a widget/shortcut can fire the deep link while auth
    // is still `loading`/`unauthenticated` (the `checkSession` round-trip hasn't
    // resolved yet). The first `router.go('/tasks')` above can then be undone by
    // the auth redirect (→ `/login` → `/home`) before the destination screen
    // ever mounts, stranding a still-set pending action. When auth settles to
    // an authed state (full OR offline), RE-DRIVE the navigation for any
    // non-chat action that is still pending so the destination finally mounts
    // and drains it. (`chat` self-clears above; a leftover non-chat action
    // means it never landed.)
    ref.listen<AuthState>(authProvider, (prev, next) {
      if (prev?.isAuthed == true) return;
      if (!next.isAuthed) return;
      final pending = ref.read(pendingActionProvider);
      if (pending == null) return;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (!mounted) return;
        // Re-read: the screen may have drained it between settle and post-frame.
        final still = ref.read(pendingActionProvider);
        if (still != null) _navigateForAction(router, still);
      });
    });

    // SILENT UPGRADE: when the backend becomes reachable again (false→true
    // edge) while the session is in offline mode, revalidate in the
    // background. checkSession skips the `loading` flash for already-authed
    // states, so the upgrade (→ authenticated) — or a revocation (→
    // unauthenticated on a real 401) — happens without UI churn. This hook
    // lives HERE (not in tasks_provider, which owns reachableProvider) because
    // tasks_provider already imports auth_provider — wiring it there would
    // create an import cycle. Covered by test/core/reconnect_upgrade_test.dart.
    ref.listen<bool>(reachableProvider, (prev, next) {
      if (prev != false || next != true) return;
      if (ref.read(authProvider).status != AuthStatus.authenticatedOffline) {
        return;
      }
      ref.read(authProvider.notifier).checkSession();
    });

    return MaterialApp.router(
      title: 'LazyClaw',
      theme: buildAppTheme(),
      routerConfig: router,
      // flutter_quill (native Docs editor) needs its localization delegates +
      // the Global* delegates it bundles for the editor/toolbar to render.
      localizationsDelegates: FlutterQuillLocalizations.localizationsDelegates,
      supportedLocales: FlutterQuillLocalizations.supportedLocales,
    );
  }
}
