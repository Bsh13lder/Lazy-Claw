// Widget tests for the Tasks "Projects" view: project buckets with counts that
// expand to reveal their tasks (reusing TaskRow).

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/screens/tasks/tasks_project_view.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

Task _task(
  String id,
  String title, {
  String? category,
  String status = 'todo',
}) =>
    Task(
      id: id,
      userId: 'u1',
      title: title,
      category: category,
      priority: 'medium',
      status: status,
      owner: 'user',
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

Widget _host({
  required List<Task> tasks,
  required List<Project> projects,
  void Function(Task task)? onOpen,
}) =>
    MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        body: TasksProjectView(
          tasks: tasks,
          projects: projects,
          dirtyIds: const {},
          onComplete: (_) {},
          onDelete: (_) {},
          onOpen: onOpen ?? (_) {},
        ),
      ),
    );

void main() {
  testWidgets('lists project buckets with open/total counts', (tester) async {
    await tester.pumpWidget(_host(
      tasks: [
        _task('a', 'Pay rent', category: 'Home'),
        _task('b', 'Fix sink', category: 'Home', status: 'done'),
        _task('c', 'Loose task'), // → Uncategorized
      ],
      projects: [_project('p1', 'Home', color: '#FF0000'), _project('p2', 'Work')],
    ));
    await tester.pump();

    // Buckets for both projects + Uncategorized are present.
    expect(find.byKey(const ValueKey('project-bucket-Home')), findsOneWidget);
    expect(find.byKey(const ValueKey('project-bucket-Work')), findsOneWidget);
    expect(find.byKey(const ValueKey('project-bucket-Uncategorized')),
        findsOneWidget);

    // Home: 1 open of 2 total. Work: 0/0.
    expect(find.text('1/2'), findsOneWidget);
    expect(find.text('0/0'), findsOneWidget);

    // Collapsed by default: the bucket's tasks are not yet shown.
    expect(find.text('Pay rent'), findsNothing);
  });

  testWidgets('tapping a bucket expands its tasks', (tester) async {
    await tester.pumpWidget(_host(
      tasks: [
        _task('a', 'Pay rent', category: 'Home'),
        _task('b', 'Fix sink', category: 'Home'),
      ],
      projects: [_project('p1', 'Home', color: '#FF0000')],
    ));
    await tester.pump();

    expect(find.text('Pay rent'), findsNothing);

    await tester.tap(find.byKey(const ValueKey('project-bucket-Home')));
    await tester.pumpAndSettle();

    expect(find.text('Pay rent'), findsOneWidget);
    expect(find.text('Fix sink'), findsOneWidget);
  });

  testWidgets('opening a task from an expanded bucket fires onOpen',
      (tester) async {
    Task? opened;
    await tester.pumpWidget(_host(
      tasks: [_task('a', 'Pay rent', category: 'Home')],
      projects: [_project('p1', 'Home')],
      onOpen: (t) => opened = t,
    ));
    await tester.pump();

    await tester.tap(find.byKey(const ValueKey('project-bucket-Home')));
    await tester.pumpAndSettle();

    // Tap the task's static priority chip (card body) → opens detail.
    await tester.tap(find.text('Pay rent'));
    await tester.pumpAndSettle();
    expect(opened?.id, 'a');
  });

  testWidgets('shows an empty state when there are no projects or tasks',
      (tester) async {
    await tester.pumpWidget(_host(tasks: const [], projects: const []));
    await tester.pump();

    expect(find.text('No projects yet'), findsOneWidget);
  });
}
