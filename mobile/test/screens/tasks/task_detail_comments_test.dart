// D4 — task-level COMMENTS are no longer a section pinned below sub-tasks at
// the very bottom of the sheet. They are an icon + count beside NOTES at the
// top, opening the SAME comments sheet the per-sub-task badge already used.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_comments_section.dart';

import 'task_detail_harness.dart';

const _noComments = Task(
  id: 'task-1',
  userId: 'u1',
  title: 'Quiet task',
  priority: 'medium',
  status: 'todo',
  owner: 'user',
  nagCount: 0,
  createdAt: '2026-06-06T00:00:00Z',
);

/// Two task-level comments plus one pinned to a sub-task — the sub-task one
/// must never leak into the task-level thread or its count.
const _mixed = Task(
  id: 'task-1',
  userId: 'u1',
  title: 'Busy task',
  priority: 'medium',
  status: 'todo',
  owner: 'user',
  nagCount: 0,
  createdAt: '2026-06-06T00:00:00Z',
  steps: '[{"id":"s1","title":"Step one","done":false}]',
  comments:
      '[{"id":"c1","ts":"2026-08-01T10:00:00Z","author":"user",'
      '"text":"task level one"},'
      '{"id":"c2","ts":"2026-08-01T11:00:00Z","author":"agent",'
      '"text":"task level two"},'
      '{"id":"c3","ts":"2026-08-01T12:00:00Z","author":"user",'
      '"text":"pinned to a step","subtask_id":"s1"}]',
);

void main() {
  Future<StubTasksNotifier> open(
    WidgetTester tester, {
    Task task = _noComments,
  }) async {
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(900, 2400);
    addTearDown(tester.view.reset);

    final tasks = makeTasksStub(task);
    await tester.pumpWidget(
      detailSheetHost(tasks: tasks, budgets: makeBudgetsStub(), task: task),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    return tasks;
  }

  /// The text rendered INSIDE the task-level badge. Scoped by key because a
  /// sub-task's own comment count can render the same digit elsewhere.
  String badgeCount(WidgetTester tester) => tester
      .widget<Text>(
        find.descendant(
          of: find.byKey(kTaskCommentsBadgeKey),
          matching: find.byType(Text),
        ),
      )
      .data!;

  Future<void> openComments(WidgetTester tester) async {
    await tester.ensureVisible(find.byKey(kTaskCommentsBadgeKey));
    await tester.tap(find.byKey(kTaskCommentsBadgeKey));
    await tester.pumpAndSettle();
  }

  testWidgets(
    'the badge replaces the bottom section — no inline composer remains on '
    'the main scroll',
    (tester) async {
      await open(tester);

      expect(find.byKey(kTaskCommentsBadgeKey), findsOneWidget);
      // The composer only exists inside the popup now.
      expect(find.byKey(const Key('comment-input')), findsNothing);
      expect(find.text('COMMENTS'), findsNothing);
      // Empty reads as a label, not as a bare "0".
      expect(find.text('Comments'), findsOneWidget);
    },
  );

  testWidgets(
    'the badge counts ONLY task-level comments (a sub-task comment is not '
    'one of them)',
    (tester) async {
      await open(tester, task: _mixed);
      expect(badgeCount(tester), '2');
    },
  );

  testWidgets('the popup shows task-level comments and hides sub-task ones', (
    tester,
  ) async {
    await open(tester, task: _mixed);
    await openComments(tester);

    expect(find.text('task level one'), findsOneWidget);
    expect(find.text('task level two'), findsOneWidget);
    expect(find.text('pinned to a step'), findsNothing);
  });

  testWidgets(
    'adding from the popup writes a TASK-level comment and the badge count '
    'updates once the popup closes',
    (tester) async {
      final stub = await open(tester);
      await openComments(tester);

      await tester.enterText(
        find.byKey(const Key('comment-input')),
        'first note',
      );
      await tester.tap(find.byKey(const Key('comment-send')));
      await tester.pumpAndSettle();

      expect(stub.commentAdds, hasLength(1));
      expect(stub.commentAdds.single['taskId'], 'task-1');
      expect(stub.commentAdds.single['text'], 'first note');
      // No subtaskId: this is the task's own thread.
      expect(stub.commentAdds.single['subtaskId'], isNull);

      // Close the popup — the sheet behind it re-reads the live task.
      await tester.tapAt(const Offset(20, 20));
      await tester.pumpAndSettle();
      expect(badgeCount(tester), '1');
    },
  );

  testWidgets('deleting from the popup removes it and drops the count', (
    tester,
  ) async {
    final stub = await open(tester, task: _mixed);
    await openComments(tester);

    await tester.longPress(find.byKey(const ValueKey('comment-c1')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Delete'));
    await tester.pumpAndSettle();

    expect(stub.commentDeletes, ['c1']);
    expect(find.text('task level one'), findsNothing);

    await tester.tapAt(const Offset(20, 20));
    await tester.pumpAndSettle();
    // Scoped to the badge: the sub-task's own 💬 count also reads "1".
    expect(badgeCount(tester), '1');
  });

  testWidgets('the popup still offers the add-link affordance', (tester) async {
    await open(tester);
    await openComments(tester);

    expect(find.byKey(const Key('comment-add-link')), findsOneWidget);
  });

  group('taskLevelComments (pure)', () {
    test('keeps only comments with no subtaskId, in order', () {
      final kept = taskLevelComments(_mixed.taskComments);
      expect(kept.map((c) => c.id), ['c1', 'c2']);
    });

    test('never mutates the input list', () {
      final input = _mixed.taskComments;
      final before = input.length;
      taskLevelComments(input);
      expect(input, hasLength(before));
    });
  });
}
