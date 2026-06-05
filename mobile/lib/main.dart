import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:sqflite_sqlcipher/sqflite.dart';
import 'core/config/server_config.dart';
import 'core/router/app_router.dart';
import 'local/app_db.dart';
import 'ui/app_theme.dart';
import 'providers/auth_provider.dart';
import 'providers/tasks_provider.dart';
import 'sync/background_sync.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final baseUrl = await ServerConfig.load();

  // Open the encrypted offline DB up front so the Tasks tab is instant and
  // works with the backend unreachable. If it fails (e.g. keychain locked),
  // fall back gracefully — the app still runs, just without the local cache.
  Database? db;
  try {
    db = await openAppDb();
  } catch (_) {
    db = null;
  }

  // Best-effort periodic background sync (~30 min). Never blocks startup.
  await registerBackgroundSync();

  runApp(ProviderScope(
    overrides: [
      baseUrlProvider.overrideWith((ref) => baseUrl),
      if (db != null) appDatabaseProvider.overrideWithValue(db),
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
  @override
  void initState() {
    super.initState();
    Future.microtask(() => ref.read(authProvider.notifier).checkSession());
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
