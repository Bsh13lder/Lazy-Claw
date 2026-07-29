// Regression tests for defect D1 — "opening a repeating task and hitting Save
// silently destroyed its reminder".
//
// A reminder is modelled as `due − lead`, so `resolveReminderAt` can only answer
// `''` (the CLEAR sentinel) when the due date carries NO time-of-day. The detail
// sheet fed that composed value into `updateTask` unconditionally, so for any
// DATE-ONLY due — which is the shape of every backend-respawned recurring
// occurrence, and of every Smart-Rescheduled task — a save that only touched the
// notes or the priority permanently deleted `reminder_at`, its server reminder
// job and its advance nags. The sheet also rendered no reminder at all for those
// tasks, so the user could not even see what was about to be destroyed.
//
// Harness mirrors task_detail_sheet_test.dart: the sheet is opened through the
// public showTaskDetailSheet helper with tasksProvider overridden by a stub
// notifier that records updateTask calls, and the stub's TaskDao is backed by a
// noSuchMethod fake Database so no real sqflite isolate (whose timer would hang
// pumpAndSettle under FakeAsync) is ever spun up.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart';
import 'package:lazyclaw_mobile/repositories/tasks_repository.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_detail_sheet.dart';
import 'package:lazyclaw_mobile/sync/task_sync.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:sqflite_common/sqlite_api.dart';

class _OfflineTransport implements TasksTransport {
  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async => throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  ) async => throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> patchJson(
    String path,
    Map<String, dynamic> body,
  ) async => throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> putJson(
    String path,
    Map<String, dynamic> body,
  ) async => throw ApiError(0, 'offline');
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async =>
      throw ApiError(0, 'offline');
}

class _NoopSync extends TaskSync {
  _NoopSync(super.dao, super.repo);
  @override
  Future<SyncResult> sync({bool retryRejected = false}) async =>
      const SyncResult();
}

class _StubTasksNotifier extends TasksNotifier {
  _StubTasksNotifier(super.dao, super.sync);

  final List<Map<String, dynamic>> updateCalls = [];

  @override
  Future<void> updateTask(
    String id, {
    String? title,
    String? description,
    String? priority,
    String? dueDate,
    String? category,
    String? steps,
    String? reminderAt,
    String? recurring,
    String? tags,
    double? allocatedBudget,
    bool clearAllocatedBudget = false,
  }) async {
    updateCalls.add({
      'id': id,
      'title': title,
      'dueDate': dueDate,
      'reminderAt': reminderAt,
    });
  }
}

class _FakeDatabase implements Database {
  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('Fake DB must not be touched in this test');
}

_StubTasksNotifier _stub() {
  final dao = TaskDao(_FakeDatabase());
  return _StubTasksNotifier(
    dao,
    _NoopSync(dao, TasksRepository(_OfflineTransport())),
  );
}

/// The canonical broken shape: a respawned recurring occurrence. Its `due_date`
/// is DATE-ONLY while a real timed `reminder_at` carries the 9:00 AM nag.
const _respawned = Task(
  id: 'task-rec',
  userId: 'u1',
  title: 'Water the plants',
  description: 'Original notes',
  priority: 'medium',
  status: 'todo',
  owner: 'user',
  dueDate: '2026-06-10',
  reminderAt: '2026-06-10T09:00:00',
  recurring: '0 9 * * *',
  nagCount: 0,
  createdAt: '2026-06-06T00:00:00Z',
);

/// A timed-due task with NO reminder yet — the untouched-save path that must
/// keep applying the global default lead (regression guard for the fix).
const _timed = Task(
  id: 'task-timed',
  userId: 'u1',
  title: 'Call the dentist',
  priority: 'medium',
  status: 'todo',
  owner: 'user',
  dueDate: '2026-06-10T17:00:00',
  nagCount: 0,
  createdAt: '2026-06-06T00:00:00Z',
);

void main() {
  Widget host(_StubTasksNotifier stub, Task task) => ProviderScope(
    overrides: [tasksProvider.overrideWith((ref) => stub)],
    child: MaterialApp(
      theme: buildAppTheme(),
      home: Consumer(
        builder: (ctx, ref, _) => Scaffold(
          body: Center(
            child: ElevatedButton(
              onPressed: () => showTaskDetailSheet(ctx, ref, task),
              child: const Text('open'),
            ),
          ),
        ),
      ),
    ),
  );

  Future<void> openSheet(WidgetTester tester, _StubTasksNotifier stub, Task t) async {
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(800, 2400);
    addTearDown(tester.view.reset);
    await tester.pumpWidget(host(stub, t));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
  }

  Future<void> save(WidgetTester tester) async {
    await tester.ensureVisible(find.byKey(const Key('task-detail-save')));
    await tester.tap(find.byKey(const Key('task-detail-save')));
    await tester.pumpAndSettle();
  }

  String isoFor(DateTime d) =>
      '${d.year.toString().padLeft(4, '0')}-'
      '${d.month.toString().padLeft(2, '0')}-'
      '${d.day.toString().padLeft(2, '0')}';

  testWidgets(
    'a notes-only edit on a DATE-ONLY due task leaves reminder_at untouched',
    (tester) async {
      final stub = _stub();
      await openSheet(tester, stub, _respawned);

      await tester.enterText(
        find.byKey(const Key('task-detail-notes')),
        'Tweaked the note',
      );
      await save(tester);

      expect(stub.updateCalls, hasLength(1));
      // null = "field untouched". Anything else (especially '') destroys the
      // reminder, its server job and its advance nags.
      expect(
        stub.updateCalls.single['reminderAt'],
        isNull,
        reason: 'an untouched reminder must NOT ride the patch',
      );
    },
  );

  testWidgets('the sheet DISPLAYS the existing reminder for a date-only due', (
    tester,
  ) async {
    final stub = _stub();
    await openSheet(tester, stub, _respawned);

    final row = find.byKey(const Key('task-detail-reminder'));
    expect(row, findsOneWidget, reason: 'a set reminder must be visible');
    expect(tester.widget<Text>(row).data, contains('9:00 AM'));
  });

  testWidgets('tapping the reminder ✕ then Save sends the "" clear sentinel', (
    tester,
  ) async {
    final stub = _stub();
    await openSheet(tester, stub, _respawned);

    await tester.ensureVisible(
      find.byKey(const Key('task-detail-reminder-clear')),
    );
    await tester.tap(find.byKey(const Key('task-detail-reminder-clear')));
    await tester.pumpAndSettle();

    // The row disappears once cleared, so the user can see the reminder is gone.
    expect(find.byKey(const Key('task-detail-reminder')), findsNothing);

    await save(tester);
    expect(stub.updateCalls.single['reminderAt'], '');
  });

  testWidgets('moving the DAY re-anchors the reminder to the same clock time', (
    tester,
  ) async {
    final stub = _stub();
    await openSheet(tester, stub, _respawned);

    // Tap the "Tomorrow" quick-pick: the due stays DATE-ONLY but moves day.
    await tester.tap(find.text('Tomorrow'));
    await tester.pumpAndSettle();
    await save(tester);

    final tomorrow = isoFor(DateTime.now().add(const Duration(days: 1)));
    expect(stub.updateCalls.single['dueDate'], tomorrow);
    expect(stub.updateCalls.single['reminderAt'], '${tomorrow}T09:00:00');
  });

  testWidgets('clearing the DUE DATE entirely also clears the reminder', (
    tester,
  ) async {
    final stub = _stub();
    await openSheet(tester, stub, _respawned);

    // Tap "Tomorrow" twice: the chip toggles the day ON then back OFF, leaving
    // no due date at all. A reminder with nothing to remind about is an orphan.
    await tester.tap(find.text('Tomorrow'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Tomorrow'));
    await tester.pumpAndSettle();

    await save(tester);
    expect(stub.updateCalls.single['dueDate'], '');
    expect(stub.updateCalls.single['reminderAt'], '');
  });

  testWidgets('a timed due with no reminder still gets the default lead', (
    tester,
  ) async {
    final stub = _stub();
    await openSheet(tester, stub, _timed);

    await save(tester);

    // kDefaultReminderLead is 30 min → 17:00 − 0:30.
    expect(stub.updateCalls.single['reminderAt'], '2026-06-10T16:30:00');
  });
}
