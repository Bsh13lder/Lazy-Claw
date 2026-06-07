// Smoke test for the Tasks calendar view.
//
// Bar: pump TasksScreen with stub tasks + budgets providers, toggle from List to
// Calendar, and assert the calendar renders (TableCalendar present, a day's
// tasks list shows). Setup mirrors tasks_polish_smoke_test.dart: stub notifiers
// override every DAO/network method so their DAOs can be backed by a
// noSuchMethod fake Database, and the reachability provider is overridden with a
// no-op probe so the connectivity_plus platform channel is never hit.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/budgets_dao.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/providers/budgets_provider.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart';
import 'package:lazyclaw_mobile/repositories/budgets_repository.dart';
import 'package:lazyclaw_mobile/repositories/tasks_repository.dart';
import 'package:lazyclaw_mobile/screens/tasks_screen.dart';
import 'package:lazyclaw_mobile/sync/budgets_sync.dart';
import 'package:lazyclaw_mobile/sync/reachability.dart';
import 'package:lazyclaw_mobile/sync/task_sync.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:sqflite_sqlcipher/sqflite.dart';
import 'package:table_calendar/table_calendar.dart';

// ── Offline transports (never reached — syncs are no-op'd) ───────────────────

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

class _NoopTaskSync extends TaskSync {
  _NoopTaskSync(super.dao, super.repo);
  @override
  Future<SyncResult> sync() async => const SyncResult();
}

class _NoopBudgetsSync extends BudgetsSync {
  _NoopBudgetsSync(super.dao, super.repo);
  @override
  Future<BudgetsSyncResult> sync() async => const BudgetsSyncResult();
}

// ── Stub notifiers ───────────────────────────────────────────────────────────

class _StubTasksNotifier extends TasksNotifier {
  _StubTasksNotifier(super.dao, super.sync, List<Task> tasks) {
    state = TasksState(tasks: tasks);
  }
  @override
  Future<void> load() async {}
  @override
  Future<void> refresh() async {}
  @override
  Future<void> syncNow() async {}
  @override
  Future<void> completeTask(String id) async {}
  @override
  Future<void> deleteTask(String id) async {}
}

class _StubBudgetsNotifier extends BudgetsNotifier {
  _StubBudgetsNotifier(super.dao, super.sync, List<Project> projects) {
    state = BudgetsState(projects: projects);
  }
  @override
  Future<void> load() async {}
  @override
  Future<void> refresh() async {}
  @override
  Future<void> syncNow() async {}
}

/// A Database that throws on any access — the stubs never touch it.
class _FakeDatabase implements Database {
  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('Fake DB must not be touched in this test');
}

/// Always-offline, timer-free probe so reachableProvider builds without hitting
/// the connectivity_plus platform channel.
class _NopProbe implements ConnectivityProbe {
  @override
  Stream<bool> get onChanged => const Stream.empty();
  @override
  Future<bool> hasLink() async => false;
  @override
  Future<bool> pingHost() async => false;
}

_StubTasksNotifier _stubTasks(List<Task> tasks) {
  final dao = TaskDao(_FakeDatabase());
  return _StubTasksNotifier(
    dao,
    _NoopTaskSync(dao, TasksRepository(_OfflineTasksTransport())),
    tasks,
  );
}

_StubBudgetsNotifier _stubBudgets(List<Project> projects) {
  final dao = BudgetsDao(_FakeDatabase());
  return _StubBudgetsNotifier(
    dao,
    _NoopBudgetsSync(dao, BudgetsRepository(_OfflineBudgetsTransport())),
    projects,
  );
}

String _isoForToday() {
  final d = DateTime.now();
  return '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';
}

Task _task(String id, String title, {String? dueDate, String? category}) => Task(
      id: id,
      userId: 'u1',
      title: title,
      category: category,
      priority: 'medium',
      status: 'todo',
      owner: 'user',
      dueDate: dueDate,
      nagCount: 0,
      createdAt: '2026-06-06T00:00:00Z',
    );

Project _project(String id, String name, {String? color}) => Project(
      id: id,
      name: name,
      budget: 0,
      currency: 'USD',
      status: 'active',
      color: color,
    );

void main() {
  testWidgets('TasksScreen toggles to Calendar and renders the month grid',
      (tester) async {
    // Tasks now carry a Tasks|Notes top segment above the List|Calendar toggle,
    // so the calendar + selected-day list is one row taller. Use a roomier
    // surface than the 800×600 default so the (lazily-built) selected-day task
    // stays within the calendar list's cache extent for the assertion below.
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(800, 1200);
    addTearDown(tester.view.reset);

    // A task due today so the selected-day (today) list has content.
    final tasksStub = _stubTasks([
      _task('t1', 'Pay rent', dueDate: _isoForToday(), category: 'home'),
    ]);
    final budgetsStub =
        _stubBudgets([_project('p1', 'Home', color: '#FF0000')]);

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          tasksProvider.overrideWith((ref) => tasksStub),
          budgetsProvider.overrideWith((ref) => budgetsStub),
          reachabilityProvider.overrideWithValue(Reachability(_NopProbe())),
        ],
        child: MaterialApp(
          theme: buildAppTheme(),
          home: const TasksScreen(),
        ),
      ),
    );

    // Settles the list-view entrance animation before we switch views.
    await tester.pump(const Duration(milliseconds: 400));
    await tester.pump(const Duration(milliseconds: 400));

    // The toggle is present; Calendar isn't mounted yet.
    expect(find.text('Calendar'), findsOneWidget);
    expect(find.byType(TableCalendar<Task>), findsNothing);

    // Switch to Calendar.
    await tester.tap(find.text('Calendar'));
    await tester.pumpAndSettle();

    // Calendar view built without error and the selected day's task shows.
    expect(tester.takeException(), isNull);
    expect(find.byType(TableCalendar<Task>), findsOneWidget);
    expect(find.text('Pay rent'), findsOneWidget);
  });
}
