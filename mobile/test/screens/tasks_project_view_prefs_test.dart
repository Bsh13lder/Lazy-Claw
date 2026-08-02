// Widget tests for the Projects-view persisted state: initial expansion seeded
// from a prior session, expansion-change notifications, and the hide-completed
// filter + its eye toggle. TasksProjectView stays provider-free (all prefs I/O
// lives in TasksScreen) so this pumps it directly with plain callbacks — no
// real database, no riverpod overrides.

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
}) => Task(
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
  Set<String> initialExpanded = const <String>{},
  ValueChanged<Set<String>>? onExpandedChanged,
  bool hideCompleted = false,
  ValueChanged<bool>? onHideCompletedChanged,
}) => MaterialApp(
  theme: buildAppTheme(),
  home: Scaffold(
    body: TasksProjectView(
      tasks: tasks,
      projects: projects,
      dirtyIds: const {},
      onComplete: (_) {},
      onDelete: (_) {},
      onOpen: (_) {},
      initialExpanded: initialExpanded,
      onExpandedChanged: onExpandedChanged,
      hideCompleted: hideCompleted,
      onHideCompletedChanged: onHideCompletedChanged,
    ),
  ),
);

void main() {
  final tasks = [
    _task('a', 'Buy stamps', category: 'Errands'),
    _task('b', 'Post letter', category: 'Errands', status: 'done'),
    _task('c', 'Pay rent', category: 'Home'),
  ];
  final projects = [
    _project('p1', 'Errands', color: '#FF0000'),
    _project('p2', 'Home', color: '#00FF00'),
  ];

  testWidgets('initialExpanded renders that bucket expanded on first frame', (
    tester,
  ) async {
    await tester.pumpWidget(
      _host(
        tasks: tasks,
        projects: projects,
        initialExpanded: const {'Errands'},
      ),
    );
    await tester.pump();

    // Errands was pre-expanded — its tasks show without any tap.
    expect(find.text('Buy stamps'), findsOneWidget);
    expect(find.text('Post letter'), findsOneWidget);

    // Home was not in initialExpanded — still collapsed.
    expect(find.text('Pay rent'), findsNothing);
  });

  testWidgets('tapping a bucket header fires onExpandedChanged with the '
      'updated set', (tester) async {
    Set<String>? changed;
    await tester.pumpWidget(
      _host(
        tasks: tasks,
        projects: projects,
        onExpandedChanged: (s) => changed = s,
      ),
    );
    await tester.pump();

    await tester.tap(find.byKey(const ValueKey('project-bucket-Errands')));
    await tester.pumpAndSettle();

    expect(changed, {'Errands'});

    await tester.tap(find.byKey(const ValueKey('project-bucket-Home')));
    await tester.pumpAndSettle();

    expect(changed, {'Errands', 'Home'});

    // Collapsing again reports the shrunk set.
    await tester.tap(find.byKey(const ValueKey('project-bucket-Errands')));
    await tester.pumpAndSettle();

    expect(changed, {'Home'});
  });

  testWidgets('hideCompleted removes done rows from an expanded bucket but the '
      'header badge keeps the full open/total count', (tester) async {
    await tester.pumpWidget(
      _host(
        tasks: tasks,
        projects: projects,
        initialExpanded: const {'Errands'},
        hideCompleted: true,
      ),
    );
    await tester.pump();

    // The done task is hidden from the expanded body...
    expect(find.text('Buy stamps'), findsOneWidget);
    expect(find.text('Post letter'), findsNothing);

    // ...but the badge still reflects the FULL bucket (1 open of 2 total).
    expect(find.text('1/2'), findsOneWidget);
  });

  // Regression test mirroring TaskSection's didUpdateWidget fix in
  // tasks_screen.dart: the List view renders its sections/buckets
  // synchronously on the very first build, before the screen's async prefs
  // load (UiPrefsDao) resolves — so this State is already mounted with the
  // "pre-load" seed by the time the parent rebuilds with the real persisted
  // `initialExpanded`. Flutter reuses the mounted State for the same widget
  // type/slot (no key), so `initState` does NOT re-run on that second
  // build — without a didUpdateWidget resync, the persisted value would be
  // silently ignored forever.
  testWidgets(
    'a persisted initialExpanded arriving AFTER first mount (same widget '
    'slot, no key) resyncs the expanded set',
    (tester) async {
      // First frame: pre-load default (nothing expanded yet).
      await tester.pumpWidget(
        _host(tasks: tasks, projects: projects, initialExpanded: const {}),
      );
      await tester.pump();
      expect(find.text('Buy stamps'), findsNothing);

      // Same widget slot, no key — simulates the parent's setState once its
      // async UiPrefsDao read resolves with the real persisted set.
      await tester.pumpWidget(
        _host(
          tasks: tasks,
          projects: projects,
          initialExpanded: const {'Errands'},
        ),
      );
      await tester.pump();

      expect(find.text('Buy stamps'), findsOneWidget);
      expect(find.text('Post letter'), findsOneWidget);
    },
  );

  testWidgets('an eye toggle is present and fires onHideCompletedChanged', (
    tester,
  ) async {
    bool? toggled;
    await tester.pumpWidget(
      _host(
        tasks: tasks,
        projects: projects,
        hideCompleted: false,
        onHideCompletedChanged: (v) => toggled = v,
      ),
    );
    await tester.pump();

    expect(find.byIcon(Icons.visibility_outlined), findsOneWidget);

    await tester.tap(find.byIcon(Icons.visibility_outlined));
    await tester.pumpAndSettle();

    expect(toggled, isTrue);
  });
}
