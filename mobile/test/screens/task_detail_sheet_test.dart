// Widget tests for the tap-a-task detail/edit sheet.
//
// The sheet is opened via the public showTaskDetailSheet helper (so the modal
// route + pop behaviour is exercised end-to-end) with tasksProvider overridden
// by a stub notifier that records updateTask / deleteTask invocations. The
// stub's TaskDao is backed by a noSuchMethod fake Database so no real sqflite
// isolate is spun up (its timer would hang pumpAndSettle under FakeAsync); the
// overridden methods never touch the DAO anyway.

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

/// Records the editor's writes without touching the DAO/network.
class _StubTasksNotifier extends TasksNotifier {
  _StubTasksNotifier(super.dao, super.sync);

  final List<Map<String, dynamic>> updateCalls = [];
  final List<String> deleteCalls = [];

  @override
  Future<void> updateTask(
    String id, {
    String? title,
    String? description,
    String? priority,
    String? dueDate,
    String? category,
  }) async {
    updateCalls.add({
      'id': id,
      'title': title,
      'description': description,
      'priority': priority,
      'dueDate': dueDate,
      'category': category,
    });
  }

  @override
  Future<void> deleteTask(String id) async => deleteCalls.add(id);
}

/// A Database that throws on any access. The stub notifier overrides every
/// method that would touch the DAO, so this is never actually invoked — it only
/// satisfies TaskDao's non-null constructor arg WITHOUT spinning up a real
/// sqflite isolate (whose timer would hang pumpAndSettle under FakeAsync).
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

const _sample = Task(
  id: 'task-42',
  userId: 'u1',
  title: 'Original title',
  description: 'Original notes',
  priority: 'high',
  status: 'todo',
  owner: 'user',
  dueDate: '2026-06-10',
  nagCount: 0,
  createdAt: '2026-06-06T00:00:00Z',
);

void main() {
  Widget host(_StubTasksNotifier stub) => ProviderScope(
        overrides: [tasksProvider.overrideWith((ref) => stub)],
        child: MaterialApp(
          theme: buildAppTheme(),
          home: Consumer(
            builder: (ctx, ref, _) => Scaffold(
              body: Center(
                child: ElevatedButton(
                  onPressed: () => showTaskDetailSheet(ctx, ref, _sample),
                  child: const Text('open'),
                ),
              ),
            ),
          ),
        ),
      );

  Future<void> openSheet(WidgetTester tester, _StubTasksNotifier stub) async {
    await tester.pumpWidget(host(stub));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
  }

  testWidgets('pre-fills the title and notes from the task', (tester) async {
    final stub = _stub();
    await openSheet(tester, stub);

    final titleField =
        tester.widget<TextField>(find.byKey(const Key('task-detail-title')));
    expect(titleField.controller!.text, 'Original title');

    final notesField =
        tester.widget<TextField>(find.byKey(const Key('task-detail-notes')));
    expect(notesField.controller!.text, 'Original notes');
  });

  testWidgets('editing the title + tapping Save invokes updateTask',
      (tester) async {
    final stub = _stub();
    await openSheet(tester, stub);

    await tester.enterText(
        find.byKey(const Key('task-detail-title')), 'Edited title');
    await tester.tap(find.byKey(const Key('task-detail-save')));
    await tester.pumpAndSettle();

    expect(stub.updateCalls, hasLength(1));
    expect(stub.updateCalls.single['id'], 'task-42');
    expect(stub.updateCalls.single['title'], 'Edited title');
    // The sheet closed after saving.
    expect(find.byKey(const Key('task-detail-title')), findsNothing);
  });

  testWidgets('Delete asks to confirm then invokes deleteTask',
      (tester) async {
    final stub = _stub();
    await openSheet(tester, stub);

    await tester.tap(find.byKey(const Key('task-detail-delete')));
    await tester.pumpAndSettle();

    // Confirmation dialog is up; nothing deleted yet.
    expect(stub.deleteCalls, isEmpty);

    // Tap the dialog's "Delete" confirm (footer button reads "Delete Task").
    await tester.tap(find.text('Delete'));
    await tester.pumpAndSettle();

    expect(stub.deleteCalls, ['task-42']);
    expect(find.byKey(const Key('task-detail-title')), findsNothing);
  });
}
