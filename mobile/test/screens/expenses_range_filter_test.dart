// Widget tests for the Expenses ledger time-range filter (Week · Month ·
// Custom). Uses in-memory SQLite (sqflite_common_ffi) + always-offline
// transports so the screen runs fully offline.
//
// Mirrors the design notes in home_screen_test.dart:
// - ALL async work runs inside tester.runAsync() (sqflite_ffi uses real timers
//   that FakeAsync never advances).
// - pumpAndSettle is avoided (LzSkeleton shimmers forever); a fixed pump loop
//   drains microtasks instead.
// - reachability is overridden with a no-op probe (no platform channel).

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:lazyclaw_mobile/core/api/api_client.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/budgets_dao.dart';
import 'package:lazyclaw_mobile/providers/auth_provider.dart'
    show apiClientProvider;
import 'package:lazyclaw_mobile/providers/budgets_provider.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart'
    show appDatabaseProvider, reachabilityProvider;
import 'package:lazyclaw_mobile/repositories/budgets_repository.dart';
import 'package:lazyclaw_mobile/screens/expenses/budget_math.dart';
import 'package:lazyclaw_mobile/screens/expenses_screen.dart';
import 'package:lazyclaw_mobile/sync/budgets_sync.dart';
import 'package:lazyclaw_mobile/sync/reachability.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

// ── Always-offline budgets transport ──────────────────────────────────────────

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

class _NopProbe implements ConnectivityProbe {
  @override
  Stream<bool> get onChanged => const Stream.empty();
  @override
  Future<bool> hasLink() async => false;
  @override
  Future<bool> pingHost() async => false;
}

// ── DB helpers ────────────────────────────────────────────────────────────────

int _dbSeq = 0;

Future<Database> _openMemDb() => databaseFactoryFfi.openDatabase(
      'file:expense_range_${_dbSeq++}?mode=memory&cache=shared',
      options: OpenDatabaseOptions(
        version: kAppDbVersion,
        singleInstance: false,
        onCreate: (db, _) => createAppDbSchema(db),
      ),
    );

String _isoDate(DateTime d) => '${d.year.toString().padLeft(4, '0')}-'
    '${d.month.toString().padLeft(2, '0')}-'
    '${d.day.toString().padLeft(2, '0')}';

Future<Database> _seed() async {
  final db = await _openMemDb();
  const now = '2026-01-01T00:00:00';
  await db.insert('project_cache', {
    'id': 'p0',
    'name': 'Travel',
    'budget': 1000.0,
    'currency': 'EUR',
    'status': 'active',
    'spent': 0.0,
    'remaining': 1000.0,
    'created_at': now,
    'updated_at': now,
    'dirty': 0,
    'deleted': 0,
  });

  final today = DateTime.now();
  // Today → always inside both the week window and the current month.
  await db.insert('expense_cache', {
    'id': 'e_today',
    'project_id': 'p0',
    'amount': 10.0,
    'currency': 'EUR',
    'description': 'Coffee today',
    'status': 'posted',
    'spent_at': _isoDate(today),
    'created_at': '2026-01-02T00:00:00',
    'updated_at': now,
    'dirty': 0,
    'deleted': 0,
  });
  // Nine days ago → always OUTSIDE the 7-day week window (deterministic).
  await db.insert('expense_cache', {
    'id': 'e_old',
    'project_id': 'p0',
    'amount': 100.0,
    'currency': 'EUR',
    'description': 'Old lunch',
    'status': 'posted',
    'spent_at': _isoDate(today.subtract(const Duration(days: 9))),
    'created_at': '2026-01-01T00:00:00',
    'updated_at': now,
    'dirty': 0,
    'deleted': 0,
  });
  return db;
}

Widget _buildApp(Database db) {
  final budgetsDao = BudgetsDao(db);
  final router = GoRouter(
    initialLocation: '/expenses',
    routes: [
      GoRoute(path: '/expenses', builder: (_, __) => const ExpensesScreen()),
    ],
  );
  return ProviderScope(
    overrides: [
      appDatabaseProvider.overrideWithValue(db),
      apiClientProvider
          .overrideWithValue(ApiClient(baseUrl: 'http://localhost:18789')),
      budgetsDaoProvider.overrideWithValue(budgetsDao),
      budgetsSyncProvider.overrideWithValue(
          BudgetsSync(budgetsDao, BudgetsRepository(_OfflineBudgetsTransport()))),
      reachabilityProvider.overrideWithValue(Reachability(_NopProbe())),
    ],
    child: MaterialApp.router(
      theme: buildAppTheme(),
      routerConfig: router,
    ),
  );
}

Future<void> _pump(WidgetTester tester) async {
  await tester.pump();
  for (var i = 0; i < 10; i++) {
    await Future<void>.delayed(const Duration(milliseconds: 30));
    await tester.pump();
  }
}

/// Tap a chip/tab and drive the resulting (TabController/setState) animation to
/// completion. pump(Duration) advances the fake clock so the swipe finishes.
Future<void> _tapAndAnimate(WidgetTester tester, Finder target) async {
  await tester.tap(target);
  await tester.pump(); // kick off the animation
  await tester.pump(const Duration(milliseconds: 400)); // finish tab swipe
  await _pump(tester); // drain DB/provider-driven rebuilds
}

Future<void> _openLedger(WidgetTester tester) async {
  await _tapAndAnimate(tester, find.text('Ledger'));
}

void main() {
  setUpAll(sqfliteFfiInit);

  group('Expenses ledger time-range filter', () {
    testWidgets(
        'renders Today / Week / Month / All / Custom chips, Month default',
        (tester) async {
      await tester.runAsync(() async {
        await tester.pumpWidget(_buildApp(await _seed()));
        await _pump(tester);
        await _openLedger(tester);

        expect(find.text('Today'), findsOneWidget); // range chip
        expect(find.text('Week'), findsOneWidget);
        expect(find.text('Month'), findsOneWidget);
        // 'All' appears twice: the range chip + the project-filter chip.
        expect(find.text('All'), findsNWidgets(2));
        expect(find.text('Custom'), findsOneWidget);
        // Default range = Month → the Week label is NOT shown.
        expect(find.text('Last 7 days'), findsNothing);
      });
    });

    testWidgets('default Month view shows today\'s expense', (tester) async {
      await tester.runAsync(() async {
        await tester.pumpWidget(_buildApp(await _seed()));
        await _pump(tester);
        await _openLedger(tester);
        expect(find.text('Coffee today'), findsOneWidget);
      });
    });

    testWidgets('Month range shows the one-tap month stepper with chevrons',
        (tester) async {
      await tester.runAsync(() async {
        await tester.pumpWidget(_buildApp(await _seed()));
        await _pump(tester);
        await _openLedger(tester);

        // Month is the default range → the stepper chevrons are present and the
        // current-month label (from the pure helper) is shown.
        expect(find.byTooltip('Previous month'), findsOneWidget);
        expect(find.byTooltip('Next month'), findsOneWidget);
        expect(find.text(expenseRangeLabel(ExpenseRange.month)), findsOneWidget);

        // Switching to a non-month range hides the stepper.
        await _tapAndAnimate(tester, find.text('Week'));
        expect(find.byTooltip('Previous month'), findsNothing);
      });
    });

    testWidgets('switching to Week filters out the 9-days-ago expense',
        (tester) async {
      await tester.runAsync(() async {
        await tester.pumpWidget(_buildApp(await _seed()));
        await _pump(tester);
        await _openLedger(tester);

        await _tapAndAnimate(tester, find.text('Week'));

        // Today's row survives the week window; the 9-days-ago row is gone.
        expect(find.text('Coffee today'), findsOneWidget);
        expect(find.text('Old lunch'), findsNothing);
        // The resolved range label flips to the week label.
        expect(find.text('Last 7 days'), findsOneWidget);
      });
    });
  });
}
