// Widget tests for the Add Task sheet's `/` project suggestion strip:
//   * hidden while the title has no live `/token`,
//   * shows case-insensitive substring matches (max 4) + a "Create project"
//     row when the token has no exact-name match,
//   * tapping a match row strips the token from the title text and carries
//     the picked project as the category on submit.

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
  // we can assert the title/category the sheet resolved.
  String? capturedTitle;
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
                capturedTitle = r?.title;
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
    capturedTitle = null;
    capturedCategory = null;
  });

  testWidgets('no strip when the title has no project token', (tester) async {
    await tester.pumpWidget(host(projects: [_project('p1', 'Groceries')]));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField).first, 'buy milk tomorrow');
    await tester.pump();

    expect(find.byKey(const ValueKey('project-suggest-Groceries')), findsNothing);
    expect(find.byKey(const Key('project-suggest-create')), findsNothing);
  });

  testWidgets(
    'typing /gro shows the matching projects + a create row (no exact match)',
    (tester) async {
      await tester.pumpWidget(
        host(
          projects: [
            _project('p1', 'Groceries'),
            _project('p2', 'Grow lights'),
            _project('p3', 'Casa'),
          ],
        ),
      );
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();

      await tester.enterText(find.byType(TextField).first, 'buy paint /gro');
      await tester.pump();

      expect(
        find.byKey(const ValueKey('project-suggest-Groceries')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('project-suggest-Grow lights')),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('project-suggest-Casa')), findsNothing);
      expect(find.byKey(const Key('project-suggest-create')), findsOneWidget);
    },
  );

  testWidgets(
    'an exact-matching token hides the create row',
    (tester) async {
      await tester.pumpWidget(
        host(projects: [_project('p1', 'Groceries')]),
      );
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();

      await tester.enterText(
        find.byType(TextField).first,
        'buy paint /Groceries',
      );
      await tester.pump();

      expect(
        find.byKey(const ValueKey('project-suggest-Groceries')),
        findsOneWidget,
      );
      expect(find.byKey(const Key('project-suggest-create')), findsNothing);
    },
  );

  testWidgets(
    'tapping a suggestion strips the token and carries the category on submit',
    (tester) async {
      await tester.pumpWidget(
        host(projects: [_project('p1', 'Groceries')]),
      );
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();

      await tester.enterText(find.byType(TextField).first, 'buy paint /gro');
      await tester.pump();

      await tester.tap(find.byKey(const ValueKey('project-suggest-Groceries')));
      await tester.pump();

      final field = tester.widget<TextField>(find.byType(TextField).first);
      expect(field.controller!.text, 'buy paint');
      // The caret must land at the end of the stripped text (a raw `.text =`
      // assignment resets Flutter's selection to -1/invalid, which makes the
      // next keystroke land at index 0 instead of where the user was typing).
      expect(field.controller!.selection.baseOffset, 'buy paint'.length);
      expect(field.controller!.selection.extentOffset, 'buy paint'.length);

      // The submit affordance is the floating square, which is anchored to
      // the sheet's viewport — no ensureVisible() needed, and that it is
      // always hit-testable is exactly the point of it.
      await tester.tap(find.byKey(kAddTaskSubmitKey));
      await tester.pumpAndSettle();

      expect(captured, isTrue);
      expect(capturedTitle, 'buy paint');
      expect(capturedCategory, 'Groceries');
    },
  );
}
