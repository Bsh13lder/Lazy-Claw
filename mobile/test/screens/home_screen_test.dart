// Widget tests for HomeScreen — uses in-memory SQLite (sqflite_common_ffi)
// and always-offline transports so tests run fully offline.
//
// Design notes:
// - ALL async work (DB setup + pump calls) runs inside tester.runAsync() to
//   escape FakeAsync. sqflite_ffi uses real isolates whose timers FakeAsync
//   captures but never advances, causing "Pending timers" assertion failures.
// - pumpAndSettle is NOT used: LzSkeleton has an infinite shimmer animation.
//   _pumpHome(tester) pumps a fixed frame sequence that drains microtasks.
// - reachabilityProvider is overridden with a fake ConnectivityProbe that
//   never calls the connectivity_plus platform channel (which has no test
//   implementation and throws MissingPluginException).

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:lazyclaw_mobile/chat/chat_controller.dart';
import 'package:lazyclaw_mobile/chat/chat_message.dart';
import 'package:lazyclaw_mobile/chat/chat_socket.dart';
import 'package:lazyclaw_mobile/chat/ws_frames.dart';
import 'package:lazyclaw_mobile/core/api/api_client.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/budgets_dao.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/providers/auth_provider.dart'
    show apiClientProvider;
import 'package:lazyclaw_mobile/providers/budgets_provider.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart';
import 'package:lazyclaw_mobile/repositories/budgets_repository.dart';
import 'package:lazyclaw_mobile/repositories/tasks_repository.dart';
import 'package:lazyclaw_mobile/screens/chat_screen.dart'
    show chatControllerProvider;
import 'package:lazyclaw_mobile/screens/home_screen.dart';
import 'package:lazyclaw_mobile/sync/budgets_sync.dart';
import 'package:lazyclaw_mobile/sync/reachability.dart';
import 'package:lazyclaw_mobile/sync/task_sync.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

// ── Always-offline transports ─────────────────────────────────────────────────

class _OfflineTasksTransport implements TasksTransport {
  @override
  Future<Map<String, dynamic>> getJson(String path,
          {Map<String, dynamic>? queryParams}) async =>
      throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> postJson(
          String path, Map<String, dynamic> body) async =>
      throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> patchJson(
          String path, Map<String, dynamic> body) async =>
      throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async =>
      throw ApiError(0, 'offline');
}

class _OfflineBudgetsTransport implements BudgetsTransport {
  @override
  Future<Map<String, dynamic>> getJson(String path,
          {Map<String, dynamic>? queryParams}) async =>
      throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> postJson(
          String path, Map<String, dynamic> body) async =>
      throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> patchJson(
          String path, Map<String, dynamic> body) async =>
      throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async =>
      throw ApiError(0, 'offline');
}

// ── No-op ConnectivityProbe (avoids connectivity_plus platform channel) ───────

/// Fake probe: always offline, empty stream. No platform channel calls.
class _NopProbe implements ConnectivityProbe {
  @override
  Stream<bool> get onChanged => const Stream.empty();
  @override
  Future<bool> hasLink() async => false;
  @override
  Future<bool> pingHost() async => false;
}

// ── No-op ChatSocket ──────────────────────────────────────────────────────────

class _NopSocket extends ChatSocket {
  final _ctrl = StreamController<ServerFrame>.broadcast();

  _NopSocket()
      : super(channelFactory: (_, __) => FakeSink(Stream.empty(), []));

  @override
  Stream<ServerFrame> get frames => _ctrl.stream;

  @override
  Future<void> connect(String url, {required String cookie}) async {}

  @override
  Future<void> dispose() async => _ctrl.close();
}

// ── DB helpers ────────────────────────────────────────────────────────────────

int _dbSeq = 0;

Future<Database> _openMemDb() => databaseFactoryFfi.openDatabase(
      'file:home_wtest_${_dbSeq++}?mode=memory&cache=shared',
      options: OpenDatabaseOptions(
        version: kAppDbVersion,
        singleInstance: false,
        onCreate: (db, _) => createAppDbSchema(db),
      ),
    );

// ── Fixture ───────────────────────────────────────────────────────────────────

class _Fixture {
  _Fixture({
    required this.db,
    required this.taskDao,
    required this.budgetsDao,
    required this.taskSync,
    required this.budgetsSync,
    required this.reachability,
  });

  final Database db;
  final TaskDao taskDao;
  final BudgetsDao budgetsDao;
  final TaskSync taskSync;
  final BudgetsSync budgetsSync;
  final Reachability reachability;
}

Future<_Fixture> _mkFixture({
  List<Task> tasks = const [],
  List<({double budget, double? spent, String currency})> projects = const [],
  // Favorited (starred) projects, inserted with is_favorite = 1 so the Home
  // "Favorites" section renders them. Each carries its own display name.
  List<({String name, double budget, String currency})> favoriteProjects =
      const [],
}) async {
  const now = '2026-01-01T00:00:00';
  final db = await _openMemDb();

  for (final t in tasks) {
    await db.insert('task_cache', {
      'id': t.id,
      'user_id': t.userId,
      'title': t.title,
      'priority': t.priority,
      'status': t.status,
      'owner': t.owner,
      'nag_count': t.nagCount,
      'created_at': t.createdAt,
      'due_date': t.dueDate,
      'dirty': 0,
      'deleted': 0,
    });
  }

  for (var i = 0; i < projects.length; i++) {
    final p = projects[i];
    await db.insert('project_cache', {
      'id': 'proj_$i',
      'name': 'Project $i',
      'budget': p.budget,
      'currency': p.currency,
      'status': 'active',
      'spent': p.spent ?? 0.0,
      'remaining': p.budget - (p.spent ?? 0.0),
      'created_at': now,
      'updated_at': now,
      'dirty': 0,
      'deleted': 0,
    });
  }

  for (var i = 0; i < favoriteProjects.length; i++) {
    final p = favoriteProjects[i];
    await db.insert('project_cache', {
      'id': 'fav_$i',
      'name': p.name,
      'budget': p.budget,
      'currency': p.currency,
      'status': 'active',
      'is_favorite': 1,
      'spent': 0.0,
      'remaining': p.budget,
      'created_at': now,
      'updated_at': now,
      'dirty': 0,
      'deleted': 0,
    });
  }

  return _Fixture(
    db: db,
    taskDao: TaskDao(db),
    budgetsDao: BudgetsDao(db),
    taskSync: TaskSync(TaskDao(db), TasksRepository(_OfflineTasksTransport())),
    budgetsSync: BudgetsSync(
        BudgetsDao(db), BudgetsRepository(_OfflineBudgetsTransport())),
    reachability: Reachability(_NopProbe()),
  );
}

Widget _buildApp(
  _Fixture f, {
  List<ChatMessage> messages = const [],
}) {
  final router = GoRouter(
    initialLocation: '/home',
    routes: [
      GoRoute(path: '/home', builder: (_, __) => const HomeScreen()),
      GoRoute(path: '/tasks', builder: (_, __) => const Scaffold()),
      GoRoute(path: '/expenses', builder: (_, __) => const Scaffold()),
      GoRoute(path: '/notes', builder: (_, __) => const Scaffold()),
      GoRoute(path: '/chat', builder: (_, __) => const Scaffold()),
    ],
  );

  return ProviderScope(
    overrides: [
      appDatabaseProvider.overrideWithValue(f.db),
      apiClientProvider
          .overrideWithValue(ApiClient(baseUrl: 'http://localhost:18789')),
      taskDaoProvider.overrideWithValue(f.taskDao),
      taskSyncProvider.overrideWithValue(f.taskSync),
      budgetsDaoProvider.overrideWithValue(f.budgetsDao),
      budgetsSyncProvider.overrideWithValue(f.budgetsSync),
      // Override the reachability object itself so _ReachableNotifier uses
      // _NopProbe instead of the real connectivity_plus platform channel.
      reachabilityProvider.overrideWithValue(f.reachability),
      chatControllerProvider.overrideWith(
        (_) => ChatController(_NopSocket())
          ..state = List.unmodifiable(messages),
      ),
    ],
    child: MaterialApp.router(
      theme: buildAppTheme(),
      routerConfig: router,
    ),
  );
}

// ── Test data helpers ─────────────────────────────────────────────────────────

Task _task({
  String id = 't1',
  String title = 'Test task',
  String priority = 'medium',
  String? dueDate,
  String status = 'todo',
}) =>
    Task(
      id: id,
      userId: 'u1',
      title: title,
      priority: priority,
      status: status,
      owner: 'user',
      nagCount: 0,
      createdAt: '2026-01-01T00:00:00',
      dueDate: dueDate,
    );

String _isoDate(DateTime d) =>
    '${d.year.toString().padLeft(4, '0')}-'
    '${d.month.toString().padLeft(2, '0')}-'
    '${d.day.toString().padLeft(2, '0')}';

String get _today => _isoDate(DateTime.now());
String get _yesterday =>
    _isoDate(DateTime.now().subtract(const Duration(days: 1)));
String get _tomorrow =>
    _isoDate(DateTime.now().add(const Duration(days: 1)));

// ── Shared pump helper inside runAsync ────────────────────────────────────────

/// Call inside tester.runAsync(). Pumps the widget tree with real-time delays
/// so that sqflite reads triggered by addPostFrameCallback can complete.
Future<void> _pumpHome(WidgetTester tester) async {
  await tester.pump(); // first build
  // addPostFrameCallback fires here; _initialLoad() starts async DB reads.
  for (var i = 0; i < 8; i++) {
    await Future<void>.delayed(const Duration(milliseconds: 30));
    await tester.pump();
  }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

void main() {
  setUpAll(sqfliteFfiInit);

  group('HomeScreen', () {
    testWidgets('renders a time-of-day greeting', (tester) async {
      await tester.runAsync(() async {
        final f = await _mkFixture();
        await tester.pumpWidget(_buildApp(f));
        await _pumpHome(tester);

        const greetings = [
          'Good morning',
          'Good afternoon',
          'Good evening',
          'Good night',
        ];
        final found =
            greetings.any((g) => find.text(g).evaluate().isNotEmpty);
        expect(found, isTrue,
            reason: 'a time-of-day greeting must be visible');
      });
    });

    testWidgets('renders subtitle text', (tester) async {
      await tester.runAsync(() async {
        final f = await _mkFixture();
        await tester.pumpWidget(_buildApp(f));
        await _pumpHome(tester);
        expect(
          find.textContaining("Here's your day at a glance"),
          findsOneWidget,
        );
      });
    });

    testWidgets('renders LzSyncBadge in the app bar', (tester) async {
      await tester.runAsync(() async {
        final f = await _mkFixture();
        await tester.pumpWidget(_buildApp(f));
        // Must pump enough for the async _initialLoad to complete before the
        // test ends, otherwise TasksNotifier.load() fires after dispose().
        await _pumpHome(tester);
        expect(find.byType(LzSyncBadge), findsOneWidget);
      });
    });

    testWidgets('shows "All clear" when no relevant tasks', (tester) async {
      await tester.runAsync(() async {
        final f = await _mkFixture(tasks: [
          _task(id: 'd1', status: 'done', dueDate: _today),
          _task(id: 'f1', dueDate: _tomorrow),
        ]);
        await tester.pumpWidget(_buildApp(f));
        await _pumpHome(tester);
        expect(find.text('All clear'), findsOneWidget);
      });
    });

    testWidgets('shows overdue task with "Overdue" subtitle', (tester) async {
      await tester.runAsync(() async {
        final f = await _mkFixture(tasks: [
          _task(id: 'o1', title: 'Pay invoice', dueDate: _yesterday),
        ]);
        await tester.pumpWidget(_buildApp(f));
        await _pumpHome(tester);
        expect(find.text('Pay invoice'), findsOneWidget);
        expect(find.text('Overdue'), findsOneWidget);
      });
    });

    testWidgets('shows due-today task', (tester) async {
      await tester.runAsync(() async {
        final f = await _mkFixture(tasks: [
          _task(id: 'td1', title: 'Call James', dueDate: _today),
        ]);
        await tester.pumpWidget(_buildApp(f));
        await _pumpHome(tester);
        expect(find.text('Call James'), findsOneWidget);
      });
    });

    testWidgets('renders LzProgressBar for budget snapshot', (tester) async {
      await tester.runAsync(() async {
        final f = await _mkFixture(
          projects: [(budget: 600, spent: 420.0, currency: 'EUR')],
        );
        await tester.pumpWidget(_buildApp(f));
        await _pumpHome(tester);
        expect(find.byType(LzProgressBar), findsOneWidget);
      });
    });

    testWidgets('hides the Favorites section when no project is starred',
        (tester) async {
      await tester.runAsync(() async {
        final f = await _mkFixture(
          projects: [(budget: 600, spent: 100.0, currency: 'EUR')],
        );
        await tester.pumpWidget(_buildApp(f));
        await _pumpHome(tester);
        // LzSection renders the title uppercased.
        expect(find.text('FAVORITES'), findsNothing);
      });
    });

    testWidgets('renders the Favorites section for a starred project',
        (tester) async {
      await tester.runAsync(() async {
        final f = await _mkFixture(
          favoriteProjects: [
            (name: 'Nima Pinned', budget: 800, currency: 'EUR'),
          ],
        );
        await tester.pumpWidget(_buildApp(f));
        await _pumpHome(tester);
        // LzSection renders the title uppercased.
        expect(find.text('FAVORITES'), findsOneWidget);
        expect(find.text('Nima Pinned'), findsOneWidget);
      });
    });

    testWidgets('shows "No messages yet" when chat is empty', (tester) async {
      await tester.runAsync(() async {
        final f = await _mkFixture();
        await tester.pumpWidget(_buildApp(f, messages: const []));
        await _pumpHome(tester);
        expect(find.text('No messages yet'), findsOneWidget);
      });
    });

    testWidgets('previews last assistant message', (tester) async {
      await tester.runAsync(() async {
        final f = await _mkFixture();
        await tester.pumpWidget(_buildApp(f, messages: const [
          ChatMessage(role: 'user', content: 'Hello'),
          ChatMessage(
              role: 'assistant', content: "I've drafted the proposal."),
        ]));
        await _pumpHome(tester);
        expect(find.textContaining('drafted the proposal'), findsOneWidget);
      });
    });

    testWidgets('renders all four quick-action tiles', (tester) async {
      await tester.runAsync(() async {
        final f = await _mkFixture();
        await tester.pumpWidget(_buildApp(f));
        await _pumpHome(tester);
        // Scroll to end to force lazy ListView to build the quick-actions row.
        await tester.drag(find.byType(ListView).first, const Offset(0, -1200));
        await tester.pump();
        await Future<void>.delayed(const Duration(milliseconds: 30));
        await tester.pump();
        expect(find.text('+ Task'), findsOneWidget);
        expect(find.text('+ Expense'), findsOneWidget);
        expect(find.text('+ Note'), findsOneWidget);
        expect(find.text('Chat'), findsWidgets);
      });
    });

    testWidgets('"See all" action is tappable and navigates', (tester) async {
      // Verifies the "See all →" link is present and tapping it fires
      // context.go('/tasks'). Full navigation completion is verified via the
      // Chat test below (same GoRouter instance); here we just confirm the
      // tap does not throw and GoRouter registers a location change.
      String? navigatedTo;
      await tester.runAsync(() async {
        final router = GoRouter(
          initialLocation: '/home',
          observers: [],
          routes: [
            GoRoute(path: '/home', builder: (_, __) => const HomeScreen()),
            GoRoute(
              path: '/tasks',
              builder: (ctx, __) {
                navigatedTo = '/tasks';
                return const Scaffold();
              },
            ),
            GoRoute(path: '/expenses', builder: (_, __) => const Scaffold()),
            GoRoute(path: '/notes', builder: (_, __) => const Scaffold()),
            GoRoute(path: '/chat', builder: (_, __) => const Scaffold()),
          ],
        );
        final f = await _mkFixture(
          tasks: [_task(id: 'td1', dueDate: _today)],
        );
        await tester.pumpWidget(ProviderScope(
          overrides: [
            appDatabaseProvider.overrideWithValue(f.db),
            apiClientProvider
                .overrideWithValue(ApiClient(baseUrl: 'http://localhost:18789')),
            taskDaoProvider.overrideWithValue(f.taskDao),
            taskSyncProvider.overrideWithValue(f.taskSync),
            budgetsDaoProvider.overrideWithValue(f.budgetsDao),
            budgetsSyncProvider.overrideWithValue(f.budgetsSync),
            reachabilityProvider.overrideWithValue(f.reachability),
            chatControllerProvider.overrideWith(
              (_) => ChatController(_NopSocket())..state = [],
            ),
          ],
          child: MaterialApp.router(
            theme: buildAppTheme(),
            routerConfig: router,
          ),
        ));
        await _pumpHome(tester);
        await tester.tap(find.text('See all →'));
        for (var i = 0; i < 10; i++) {
          await Future<void>.delayed(const Duration(milliseconds: 30));
          await tester.pump();
        }
        expect(navigatedTo, equals('/tasks'),
            reason: 'GoRouter must have navigated to /tasks');
      });
    });

    testWidgets('Chat quick-action navigates to /chat', (tester) async {
      String? navigatedTo;
      await tester.runAsync(() async {
        final router = GoRouter(
          initialLocation: '/home',
          routes: [
            GoRoute(path: '/home', builder: (_, __) => const HomeScreen()),
            GoRoute(path: '/tasks', builder: (_, __) => const Scaffold()),
            GoRoute(path: '/expenses', builder: (_, __) => const Scaffold()),
            GoRoute(path: '/notes', builder: (_, __) => const Scaffold()),
            GoRoute(
              path: '/chat',
              builder: (ctx, __) {
                navigatedTo = '/chat';
                return const Scaffold();
              },
            ),
          ],
        );
        final f = await _mkFixture();
        await tester.pumpWidget(ProviderScope(
          overrides: [
            appDatabaseProvider.overrideWithValue(f.db),
            apiClientProvider
                .overrideWithValue(ApiClient(baseUrl: 'http://localhost:18789')),
            taskDaoProvider.overrideWithValue(f.taskDao),
            taskSyncProvider.overrideWithValue(f.taskSync),
            budgetsDaoProvider.overrideWithValue(f.budgetsDao),
            budgetsSyncProvider.overrideWithValue(f.budgetsSync),
            reachabilityProvider.overrideWithValue(f.reachability),
            chatControllerProvider.overrideWith(
              (_) => ChatController(_NopSocket())..state = [],
            ),
          ],
          child: MaterialApp.router(
            theme: buildAppTheme(),
            routerConfig: router,
          ),
        ));
        await _pumpHome(tester);
        // Scroll to bring the quick-actions row into viewport.
        await tester.drag(find.byType(ListView).first, const Offset(0, -1200));
        await tester.pump();
        await Future<void>.delayed(const Duration(milliseconds: 30));
        await tester.pump();
        await tester.tap(find.text('Chat').last);
        for (var i = 0; i < 10; i++) {
          await Future<void>.delayed(const Duration(milliseconds: 30));
          await tester.pump();
        }
        expect(navigatedTo, equals('/chat'),
            reason: 'GoRouter must have navigated to /chat');
      });
    });
  });
}
