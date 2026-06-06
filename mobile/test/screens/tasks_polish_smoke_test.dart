// Smoke test for the Tasks UI-polish pass (haptics + staggered list entrance).
//
// Animations + haptics aren't meaningfully unit-testable, so the bar here is
// "no regression + it builds + renders": pump TasksScreen with a stub
// tasksProvider holding two tasks, let the entrance animation settle, then
// assert the screen builds without error and both task titles are on screen.
//
// Setup mirrors task_detail_sheet_test.dart: the stub TasksNotifier overrides
// every method that would touch the DAO, so its TaskDao can be backed by a
// noSuchMethod fake Database (no real sqflite isolate → no hung timers). The
// reachability provider is overridden with a no-op probe so the
// connectivity_plus platform channel is never hit.

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart';
import 'package:lazyclaw_mobile/repositories/tasks_repository.dart';
import 'package:lazyclaw_mobile/screens/tasks_screen.dart';
import 'package:lazyclaw_mobile/sync/reachability.dart';
import 'package:lazyclaw_mobile/sync/task_sync.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:sqflite_common/sqlite_api.dart';

// ── Offline transport (never reached — sync is no-op'd) ───────────────────────

class _OfflineTransport implements TasksTransport {
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

class _NoopSync extends TaskSync {
  _NoopSync(super.dao, super.repo);
  @override
  Future<SyncResult> sync() async => const SyncResult();
}

/// Seeds the screen with a fixed task list and neutralises every method that
/// would otherwise reach the DAO/network.
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

/// A Database that throws on any access — the stub never touches it.
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

_StubTasksNotifier _stub(List<Task> tasks) {
  final dao = TaskDao(_FakeDatabase());
  return _StubTasksNotifier(
    dao,
    _NoopSync(dao, TasksRepository(_OfflineTransport())),
    tasks,
  );
}

Task _task(String id, String title) => Task(
      id: id,
      userId: 'u1',
      title: title,
      priority: 'medium',
      status: 'todo',
      owner: 'user',
      nagCount: 0,
      createdAt: '2026-06-06T00:00:00Z',
    );

void main() {
  testWidgets('TasksScreen builds and renders task titles after entrance',
      (tester) async {
    final stub = _stub([
      _task('t1', 'Buy groceries'),
      _task('t2', 'Ship release'),
    ]);

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          tasksProvider.overrideWith((ref) => stub),
          reachabilityProvider.overrideWithValue(Reachability(_NopProbe())),
        ],
        child: MaterialApp(
          theme: buildAppTheme(),
          home: const TasksScreen(),
        ),
      ),
    );

    // Let the staggered fade+slide entrance settle. flutter_animate defers its
    // start with a zero-duration Timer (and lazy sliver layout can schedule one
    // at end-of-clock), so pump a few frames to fire every straggler and let
    // the one-shot `_entered` rebuild drop the Animate wrappers.
    await tester.pump(const Duration(milliseconds: 400));
    await tester.pump(const Duration(milliseconds: 400));
    await tester.pump(const Duration(milliseconds: 400));

    // No build error escaped, and both titles are on screen.
    expect(tester.takeException(), isNull);
    expect(find.text('Buy groceries'), findsOneWidget);
    expect(find.text('Ship release'), findsOneWidget);
  });
}
