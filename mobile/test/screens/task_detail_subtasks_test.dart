// Widget tests for the sub-task editor inside the task detail sheet:
//   * existing sub-tasks render with a done/total progress label,
//   * add / toggle / delete / inline-edit all flow into the Save payload,
//   * a title-only edit does NOT write `steps` (no churn).
//
// Mirrors task_detail_sheet_test.dart: a stub TasksNotifier records the
// updateTask call (incl. the serialized `steps`) without touching the DAO. The
// surface is enlarged so the whole sheet fits without scrolling.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/models/subtask.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart';
import 'package:lazyclaw_mobile/repositories/tasks_repository.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_detail_sheet.dart';
import 'package:lazyclaw_mobile/sync/task_sync.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:sqflite_sqlcipher/sqflite.dart';

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
    String? recurUntil,
    String? tags,
    double? allocatedBudget,
    bool clearAllocatedBudget = false,
  }) async {
    updateCalls.add({'id': id, 'title': title, 'steps': steps});
  }

  @override
  Future<void> deleteTask(String id) async {}
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

const _withSteps = Task(
  id: 'task-9',
  userId: 'u1',
  title: 'Bake a cake',
  priority: 'medium',
  status: 'todo',
  owner: 'user',
  nagCount: 0,
  createdAt: '2026-06-06T00:00:00Z',
  steps:
      '[{"id":"s1","title":"Buy flour","done":false},'
      '{"id":"s2","title":"Preheat oven","done":true}]',
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

  Future<void> openSheet(
    WidgetTester tester,
    _StubTasksNotifier stub, {
    Task task = _withSteps,
  }) async {
    // Tall surface so the whole sheet (incl. subtasks + Save) fits unscrolled.
    await tester.binding.setSurfaceSize(const Size(600, 2000));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester.pumpWidget(host(stub, task));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
  }

  Future<void> save(WidgetTester tester) async {
    await tester.tap(find.byKey(const Key('task-detail-save')));
    await tester.pumpAndSettle();
  }

  String? lastSteps(_StubTasksNotifier stub) =>
      stub.updateCalls.single['steps'] as String?;

  testWidgets('renders existing sub-tasks + a done/total progress label', (
    tester,
  ) async {
    final stub = _stub();
    await openSheet(tester, stub);

    expect(find.text('Buy flour'), findsOneWidget);
    expect(find.text('Preheat oven'), findsOneWidget);
    expect(
      find.byKey(const Key('task-detail-subtask-progress')),
      findsOneWidget,
    );
    expect(find.text('1/2'), findsOneWidget);
  });

  testWidgets('a title-only edit leaves steps untouched (null, no churn)', (
    tester,
  ) async {
    final stub = _stub();
    await openSheet(tester, stub);
    await save(tester);

    expect(stub.updateCalls, hasLength(1));
    expect(lastSteps(stub), isNull);
  });

  testWidgets('adding a sub-task persists it in the Save payload', (
    tester,
  ) async {
    final stub = _stub();
    await openSheet(tester, stub);

    await tester.enterText(
      find.byKey(const Key('subtask-add-field')),
      'Add frosting',
    );
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pumpAndSettle();

    // It shows up immediately in the editor.
    expect(find.text('Add frosting'), findsOneWidget);

    await save(tester);

    final subs = parseSubtasks(lastSteps(stub));
    expect(
      subs.map((s) => s.title),
      containsAll(['Buy flour', 'Preheat oven', 'Add frosting']),
    );
    expect(subs.firstWhere((s) => s.title == 'Add frosting').done, isFalse);
  });

  testWidgets('toggling a sub-task persists the new done state', (
    tester,
  ) async {
    final stub = _stub();
    await openSheet(tester, stub);

    await tester.tap(find.byKey(const ValueKey('subtask-toggle-s1')));
    await tester.pumpAndSettle();
    await save(tester);

    final subs = parseSubtasks(lastSteps(stub));
    expect(subs.firstWhere((s) => s.id == 's1').done, isTrue);
  });

  testWidgets('deleting a sub-task persists the removal', (tester) async {
    final stub = _stub();
    await openSheet(tester, stub);

    await tester.tap(find.byKey(const ValueKey('subtask-delete-s2')));
    await tester.pumpAndSettle();
    await save(tester);

    final subs = parseSubtasks(lastSteps(stub));
    expect(subs.map((s) => s.id), ['s1']);
  });

  testWidgets('inline-editing a sub-task title persists the new text', (
    tester,
  ) async {
    final stub = _stub();
    await openSheet(tester, stub);

    await tester.tap(find.byKey(const ValueKey('subtask-text-s1')));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const ValueKey('subtask-edit-s1')),
      'Buy bread flour',
    );
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pumpAndSettle();
    await save(tester);

    final subs = parseSubtasks(lastSteps(stub));
    expect(subs.firstWhere((s) => s.id == 's1').title, 'Buy bread flour');
  });

  testWidgets(
    'a new (unsaved) sub-task shows no comment badge while a saved one '
    'keeps it',
    (tester) async {
      final stub = _stub();
      await openSheet(tester, stub); // _withSteps: saved sub-tasks s1, s2.

      // Both pre-existing (SAVED) sub-tasks get a comment affordance — the
      // detail sheet always wires onOpenComments, and their ids are present
      // in the watched provider task's parsed steps.
      expect(find.byIcon(Icons.chat_bubble_outline), findsNWidgets(2));
      expect(find.byKey(const ValueKey('subtask-comments-s1')), findsOneWidget);
      expect(find.byKey(const ValueKey('subtask-comments-s2')), findsOneWidget);

      await tester.enterText(
        find.byKey(const Key('subtask-add-field')),
        'Add frosting',
      );
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pumpAndSettle();

      // The freshly-added sub-task renders immediately (in-sheet, unsaved)
      // but must NOT get a comment badge — the count stays at 2, not 3, since
      // a comment on it before Save would replay against an unknown
      // subtask_id server-side.
      expect(find.text('Add frosting'), findsOneWidget);
      expect(find.byIcon(Icons.chat_bubble_outline), findsNWidgets(2));
    },
  );

  testWidgets(
    'editing a sub-task allows multiple lines (maxLines: null) so a long '
    "title wraps instead of scrolling off-screen",
    (tester) async {
      final stub = _stub();
      await openSheet(tester, stub);

      await tester.tap(find.byKey(const ValueKey('subtask-text-s1')));
      await tester.pumpAndSettle();

      final field = tester.widget<TextField>(
        find.byKey(const ValueKey('subtask-edit-s1')),
      );
      expect(field.maxLines, isNull);
      // The existing commit-on-done behavior is untouched.
      expect(field.textInputAction, TextInputAction.done);
    },
  );

  testWidgets('removing the last sub-task writes an empty string (clears)', (
    tester,
  ) async {
    const oneStep = Task(
      id: 'task-10',
      userId: 'u1',
      title: 'Single',
      priority: 'medium',
      status: 'todo',
      owner: 'user',
      nagCount: 0,
      createdAt: '2026-06-06T00:00:00Z',
      steps: '[{"id":"only","title":"Only step","done":false}]',
    );
    final stub = _stub();
    await openSheet(tester, stub, task: oneStep);

    await tester.tap(find.byKey(const ValueKey('subtask-delete-only')));
    await tester.pumpAndSettle();
    await save(tester);

    // Empty string (not null) so the DAO patch includes + clears the column.
    expect(lastSteps(stub), '');
  });
}
