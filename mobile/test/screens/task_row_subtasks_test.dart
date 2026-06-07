// Widget tests for the Todoist-style TaskRow upgrades:
//   * a subtask progress badge (done/total) on the card,
//   * visually-distinct done styling (struck-through title + dimmed card),
//   * tap-the-title inline editing (commits via onTitleChanged),
//   * single AND double tap both open the detail sheet (onTap).

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_row.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

Task _task({
  required String id,
  String title = 'Sample task',
  String status = 'todo',
  String? steps,
  String? category,
}) =>
    Task(
      id: id,
      userId: 'u1',
      title: title,
      priority: 'medium',
      status: status,
      owner: 'user',
      category: category,
      steps: steps,
      nagCount: 0,
      createdAt: '2026-06-06T00:00:00Z',
    );

Widget _host(
  Task task, {
  VoidCallback? onTap,
  ValueChanged<String>? onTitleChanged,
}) =>
    MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        body: TaskRow(
          task: task,
          pendingSync: false,
          onComplete: () {},
          onDelete: () {},
          onTap: onTap,
          onTitleChanged: onTitleChanged,
        ),
      ),
    );

void main() {
  group('subtask progress badge', () {
    testWidgets('shows done/total when the task has sub-tasks', (tester) async {
      await tester.pumpWidget(_host(_task(
        id: 't1',
        steps: '[{"id":"a","title":"x","done":true},'
            '{"id":"b","title":"y","done":false}]',
      )));
      await tester.pump();

      expect(find.byKey(const ValueKey('task-row-subtasks-t1')), findsOneWidget);
      expect(find.text('1/2'), findsOneWidget);
    });

    testWidgets('hides the badge when there are no sub-tasks', (tester) async {
      await tester.pumpWidget(_host(_task(id: 't2')));
      await tester.pump();
      expect(find.byKey(const ValueKey('task-row-subtasks-t2')), findsNothing);
    });
  });

  group('done styling', () {
    testWidgets('a done task renders a struck-through title and dims the card',
        (tester) async {
      await tester.pumpWidget(_host(_task(id: 't3', status: 'done')));
      await tester.pump();

      final titleText = tester.widget<Text>(find.text('Sample task'));
      expect(titleText.style?.decoration, TextDecoration.lineThrough);

      // The whole card is wrapped in a dimming Opacity for done tasks.
      expect(find.byType(Opacity), findsWidgets);
    });
  });

  group('inline title edit', () {
    testWidgets('tapping the title enters an inline editor and commits on done',
        (tester) async {
      String? committed;
      await tester.pumpWidget(_host(
        _task(id: 't4'),
        onTitleChanged: (t) => committed = t,
        onTap: () {},
      ));
      await tester.pump();

      // Tap the title text → inline TextField appears.
      await tester.tap(find.byKey(const ValueKey('task-row-title-t4')));
      await tester.pump();
      expect(
          find.byKey(const ValueKey('task-row-title-edit-t4')), findsOneWidget);

      await tester.enterText(
          find.byKey(const ValueKey('task-row-title-edit-t4')), 'Renamed task');
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pump();

      expect(committed, 'Renamed task');
    });

    testWidgets('an unchanged commit does NOT fire onTitleChanged',
        (tester) async {
      var calls = 0;
      await tester.pumpWidget(_host(
        _task(id: 't5'),
        onTitleChanged: (_) => calls++,
        onTap: () {},
      ));
      await tester.pump();

      await tester.tap(find.byKey(const ValueKey('task-row-title-t5')));
      await tester.pump();
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pump();

      expect(calls, 0);
    });

    testWidgets('the title is plain text (no edit) when onTitleChanged is null',
        (tester) async {
      await tester.pumpWidget(_host(_task(id: 't6')));
      await tester.pump();
      // No inline-edit hotspot is wired.
      expect(find.byKey(const ValueKey('task-row-title-t6')), findsNothing);
      expect(find.text('Sample task'), findsOneWidget);
    });
  });

  group('open detail (single + double tap)', () {
    testWidgets('single tap on the card body opens the detail sheet',
        (tester) async {
      var opened = 0;
      await tester.pumpWidget(_host(_task(id: 't8'), onTap: () => opened++));
      await tester.pump();

      // Tap a static chip (card body — not the title, not the checkbox); it
      // passes through to the card's open-detail InkWell. No double-tap
      // recognizer means the tap fires instantly.
      await tester.tap(find.text('medium'));
      await tester.pump();

      expect(opened, 1);
    });

    testWidgets('double tap opens the detail sheet (first tap already opens it)',
        (tester) async {
      var opened = 0;
      await tester.pumpWidget(_host(_task(id: 't7'), onTap: () => opened++));
      await tester.pump();

      final at = tester.getCenter(find.text('medium'));
      await tester.tapAt(at);
      await tester.pump(const Duration(milliseconds: 50));
      await tester.tapAt(at);
      await tester.pump();

      expect(opened, greaterThanOrEqualTo(1));
    });
  });
}
