// The task's OWN created/completed times, surfaced in the detail sheet.
//
// `Task.createdAt` / `Task.completedAt` have existed end-to-end since the
// beginning (schema.sql → task.dart) and were simply never displayed. The
// only real rule to guard: the completion line appears when the task is
// actually DONE, and a stale `completed_at` on a re-opened task does NOT
// resurrect it.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/task.dart';

import 'task_detail_harness.dart';

const _todo = Task(
  id: 'task-9',
  userId: 'u1',
  title: 'Bake a cake',
  priority: 'medium',
  status: 'todo',
  owner: 'user',
  nagCount: 0,
  createdAt: '2026-06-06T08:00:00Z',
);

/// Same task, finished. `status` is what `Task.isDone` reads.
const _done = Task(
  id: 'task-9',
  userId: 'u1',
  title: 'Bake a cake',
  priority: 'medium',
  status: 'done',
  owner: 'user',
  nagCount: 0,
  createdAt: '2026-06-06T08:00:00Z',
  completedAt: '2026-06-09T17:30:00Z',
);

/// Re-opened: the column still carries the old completion time, but the task
/// is demonstrably not finished. Showing "Done …" here would be a lie.
const _reopened = Task(
  id: 'task-9',
  userId: 'u1',
  title: 'Bake a cake',
  priority: 'medium',
  status: 'todo',
  owner: 'user',
  nagCount: 0,
  createdAt: '2026-06-06T08:00:00Z',
  completedAt: '2026-06-09T17:30:00Z',
);

void main() {
  Future<void> open(WidgetTester tester, Task task) async {
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(900, 2400);
    addTearDown(tester.view.reset);

    await tester.pumpWidget(
      detailSheetHost(
        tasks: makeTasksStub(task),
        budgets: makeBudgetsStub(),
        task: task,
      ),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
  }

  testWidgets('shows when the task was created', (tester) async {
    await open(tester, _todo);

    expect(find.byKey(const Key('task-detail-created')), findsOneWidget);
    final label = tester
        .widget<Text>(find.byKey(const Key('task-detail-created')))
        .data;
    // The absolute branch — never a bare ISO string, never a raw "d/m/yyyy".
    expect(label, startsWith('Created '));
    expect(label, contains('Jun'));
    expect(label, isNot(contains('T08:00')));
  });

  testWidgets('hides the completion line while the task is not done', (
    tester,
  ) async {
    await open(tester, _todo);

    expect(find.byKey(const Key('task-detail-completed')), findsNothing);
  });

  testWidgets('shows the completion line once the task IS done', (
    tester,
  ) async {
    await open(tester, _done);

    expect(find.byKey(const Key('task-detail-completed')), findsOneWidget);
    final label = tester
        .widget<Text>(find.byKey(const Key('task-detail-completed')))
        .data;
    expect(label, contains('Done '));
    expect(label, contains('Jun'));
  });

  testWidgets('a re-opened task does not resurrect its old completion time', (
    tester,
  ) async {
    await open(tester, _reopened);

    expect(find.byKey(const Key('task-detail-created')), findsOneWidget);
    expect(find.byKey(const Key('task-detail-completed')), findsNothing);
  });

  testWidgets('the title field and delete action survive the extraction', (
    tester,
  ) async {
    // The header block moved into its own widget to make room for this
    // feature; every key the D-series tests drive must still resolve.
    await open(tester, _todo);

    expect(find.byKey(const Key('task-detail-title')), findsOneWidget);
    expect(find.byKey(const Key('task-detail-delete')), findsOneWidget);
  });
}
