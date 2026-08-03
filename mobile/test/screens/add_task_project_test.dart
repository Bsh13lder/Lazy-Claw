// Widget tests for the Add Task sheet's PROJECT chip:
//   * defaults to "No project" when nothing is picked or typed,
//   * picking a project from the picker updates the chip,
//   * a `/token` in the title is carried as the category when untouched,
//   * a manual pick wins over a typed token (effective-category precedence).

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/screens/tasks/add_task_sheet.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

Project _project(String id, String name, {String? color}) => Project(
  id: id,
  name: name,
  budget: 0,
  currency: 'USD',
  status: 'active',
  color: color,
);

void main() {
  // Host that opens the real showAddTaskSheet helper and captures its result so
  // we can assert the category the sheet resolved.
  String? capturedCategory;
  bool captured = false;

  Widget host({List<Project> projects = const []}) => ProviderScope(
    child: MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        body: Center(
          child: Builder(
            builder: (ctx) => ElevatedButton(
              onPressed: () async {
                final r = await showAddTaskSheet(ctx, projects: projects);
                captured = r != null;
                capturedCategory = r?.category;
              },
              child: const Text('open'),
            ),
          ),
        ),
      ),
    ),
  );

  setUp(() {
    captured = false;
    capturedCategory = null;
  });

  testWidgets('shows the PROJECT chip defaulting to "No project"', (
    tester,
  ) async {
    await tester.pumpWidget(
      host(projects: [_project('p1', 'Groceries')]),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    expect(find.text('PROJECT'), findsOneWidget);
    expect(find.byKey(const Key('add-task-project')), findsOneWidget);
    expect(find.text('No project'), findsOneWidget);
  });

  testWidgets('picking a project from the picker updates the chip', (
    tester,
  ) async {
    await tester.pumpWidget(
      host(projects: [_project('p1', 'Groceries')]),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('add-task-project')));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const ValueKey('project-pick-p1')));
    await tester.pumpAndSettle();

    expect(find.text('Groceries'), findsWidgets);
  });

  testWidgets(
    'a typed /token is carried as the category when untouched',
    (tester) async {
      await tester.pumpWidget(host());
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();

      await tester.enterText(
        find.byType(TextField).first,
        'buy paint /Groceries',
      );
      await tester.pump();

      // The submit affordance is the floating square, which is anchored to
      // the sheet's viewport — no ensureVisible() needed, and that it is
      // always hit-testable is exactly the point of it.
      await tester.tap(find.byKey(kAddTaskSubmitKey));
      await tester.pumpAndSettle();

      expect(captured, isTrue);
      expect(capturedCategory, 'Groceries');
    },
  );

  testWidgets('a manual pick beats a typed token', (tester) async {
    await tester.pumpWidget(
      host(projects: [_project('c1', 'Casa')]),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('add-task-project')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('project-pick-c1')));
    await tester.pumpAndSettle();

    await tester.enterText(
      find.byType(TextField).first,
      'buy paint /Groceries',
    );
    await tester.pump();

    // The submit affordance is the floating square, which is anchored to
    // the sheet's viewport — no ensureVisible() needed, and that it is
    // always hit-testable is exactly the point of it.
    await tester.tap(find.byKey(kAddTaskSubmitKey));
    await tester.pumpAndSettle();

    expect(captured, isTrue);
    expect(capturedCategory, 'Casa');
  });
}
