// Widget tests for the tap-the-chip quick edits + foldable subtasks on TaskRow.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/models/subtask.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_row.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

Task _task({
  required String id,
  String title = 'Sample task',
  String priority = 'medium',
  String? category,
  String? steps,
}) => Task(
  id: id,
  userId: 'u1',
  title: title,
  priority: priority,
  status: 'todo',
  owner: 'user',
  category: category,
  steps: steps,
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

Widget _host(
  Task task, {
  List<Project> projects = const [],
  ValueChanged<String>? onPriorityChanged,
  ValueChanged<String>? onDueDateChanged,
  ValueChanged<String>? onCategoryChanged,
  ValueChanged<List<Subtask>>? onSubtasksChanged,
  VoidCallback? onTap,
}) => MaterialApp(
  theme: buildAppTheme(),
  home: Scaffold(
    body: TaskRow(
      task: task,
      pendingSync: false,
      projects: projects,
      onComplete: () {},
      onDelete: () {},
      onTap: onTap,
      onPriorityChanged: onPriorityChanged,
      onDueDateChanged: onDueDateChanged,
      onCategoryChanged: onCategoryChanged,
      onSubtasksChanged: onSubtasksChanged,
    ),
  ),
);

void main() {
  group('priority chip', () {
    testWidgets('tapping opens the menu; selecting commits the new priority', (
      tester,
    ) async {
      String? committed;
      await tester.pumpWidget(
        _host(
          _task(id: 't1', priority: 'medium'),
          onPriorityChanged: (p) => committed = p,
        ),
      );
      await tester.pump();

      await tester.tap(find.byKey(const ValueKey('task-row-priority-t1')));
      await tester.pumpAndSettle();

      // The menu is open (all four levels present).
      expect(
        find.byKey(const ValueKey('priority-menu-urgent')),
        findsOneWidget,
      );

      await tester.tap(find.byKey(const ValueKey('priority-menu-high')));
      await tester.pumpAndSettle();

      expect(committed, 'high');
    });

    testWidgets('is a static label when no onPriorityChanged is wired', (
      tester,
    ) async {
      var opened = 0;
      await tester.pumpWidget(_host(_task(id: 't2'), onTap: () => opened++));
      await tester.pump();

      // No callback → the chip is non-interactive; the tap falls through to the
      // card's open-detail gesture.
      await tester.tap(find.byKey(const ValueKey('task-row-priority-t2')));
      await tester.pumpAndSettle();

      expect(opened, 1);
      expect(find.byKey(const ValueKey('priority-menu-high')), findsNothing);
    });
  });

  group('project chip', () {
    testWidgets('a project-less task shows an add-project chip that opens the '
        'picker; choosing a project commits its name', (tester) async {
      String? committed;
      await tester.pumpWidget(
        _host(
          _task(id: 't3', category: null),
          projects: [_project('p1', 'Home', color: '#FF0000')],
          onCategoryChanged: (c) => committed = c,
        ),
      );
      await tester.pump();

      expect(
        find.byKey(const ValueKey('task-row-project-add-t3')),
        findsOneWidget,
      );

      await tester.tap(find.byKey(const ValueKey('task-row-project-add-t3')));
      await tester.pumpAndSettle();

      // Picker is open: a "No project" row + the project.
      expect(find.byKey(const Key('project-pick-none')), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('project-pick-p1')));
      await tester.pumpAndSettle();

      expect(committed, 'Home');
    });

    testWidgets('a categorized task can clear its project via "No project"', (
      tester,
    ) async {
      String? committed;
      await tester.pumpWidget(
        _host(
          _task(id: 't4', category: 'Home'),
          projects: [_project('p1', 'Home', color: '#FF0000')],
          onCategoryChanged: (c) => committed = c,
        ),
      );
      await tester.pump();

      await tester.tap(find.byKey(const ValueKey('task-row-project-t4')));
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const Key('project-pick-none')));
      await tester.pumpAndSettle();

      // Empty string = clear.
      expect(committed, '');
    });
  });

  group('foldable subtasks', () {
    const steps =
        '[{"id":"a","title":"First","done":false},'
        '{"id":"b","title":"Second","done":true}]';

    testWidgets('renders folded by default and expands on tap', (tester) async {
      await tester.pumpWidget(
        _host(
          _task(id: 't5', steps: steps),
          onSubtasksChanged: (_) {},
        ),
      );
      await tester.pump();

      // Folded: the progress chip is present, the checklist is not.
      expect(
        find.byKey(const ValueKey('task-row-subtasks-t5')),
        findsOneWidget,
      );
      expect(find.text('1/2'), findsOneWidget);
      expect(find.byKey(const ValueKey('task-row-checklist-t5')), findsNothing);

      // Tap the fold chip → checklist appears.
      await tester.tap(find.byKey(const ValueKey('task-row-subtasks-t5')));
      await tester.pumpAndSettle();

      expect(
        find.byKey(const ValueKey('task-row-checklist-t5')),
        findsOneWidget,
      );
      expect(find.text('First'), findsOneWidget);
      expect(find.text('Second'), findsOneWidget);

      // Tap again → collapses.
      await tester.tap(find.byKey(const ValueKey('task-row-subtasks-t5')));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('task-row-checklist-t5')), findsNothing);
    });

    testWidgets(
      'toggling a subtask in the expanded checklist commits the list',
      (tester) async {
        List<Subtask>? committed;
        await tester.pumpWidget(
          _host(
            _task(id: 't6', steps: steps),
            onSubtasksChanged: (s) => committed = s,
          ),
        );
        await tester.pump();

        await tester.tap(find.byKey(const ValueKey('task-row-subtasks-t6')));
        await tester.pumpAndSettle();

        // Toggle the first (open) subtask done.
        await tester.tap(find.byKey(const ValueKey('subtask-toggle-a')));
        await tester.pump();

        expect(committed, isNotNull);
        expect(committed!.firstWhere((s) => s.id == 'a').done, isTrue);
      },
    );

    testWidgets('a non-editable subtask chip stays a static progress badge', (
      tester,
    ) async {
      await tester.pumpWidget(_host(_task(id: 't7', steps: steps)));
      await tester.pump();

      // No onSubtasksChanged → static badge, no fold, no checklist.
      expect(
        find.byKey(const ValueKey('task-row-subtasks-t7')),
        findsOneWidget,
      );
      expect(find.text('1/2'), findsOneWidget);
      expect(find.byKey(const ValueKey('task-row-checklist-t7')), findsNothing);
    });
  });
}
