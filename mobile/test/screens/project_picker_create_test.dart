// Widget tests for the `allowCreate` create-new affordance on
// [showProjectPicker] (Task 1 of the tasks-followup-ux plan).

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/screens/tasks/chip_edit.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

Project _project(String id, String name, {String? color}) => Project(
      id: id,
      name: name,
      budget: 0,
      currency: 'USD',
      status: 'active',
      color: color,
    );

Widget _host({
  required List<Project> projects,
  bool allowCreate = false,
  String? current,
  required ValueChanged<ProjectPickResult?> onResult,
}) =>
    MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        body: Builder(
          builder: (context) => ElevatedButton(
            onPressed: () async {
              final result = await showProjectPicker(
                context,
                projects: projects,
                current: current,
                allowCreate: allowCreate,
              );
              onResult(result);
            },
            child: const Text('open'),
          ),
        ),
      ),
    );

void main() {
  final projects = [
    _project('p1', 'Groceries', color: '#FF0000'),
    _project('p2', 'Casa', color: '#00FF00'),
  ];

  group('showProjectPicker allowCreate', () {
    testWidgets('allowCreate: true shows a trailing "＋ New project" tile',
        (tester) async {
      await tester.pumpWidget(_host(
        projects: projects,
        allowCreate: true,
        onResult: (_) {},
      ));
      await tester.pump();

      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('project-pick-create')), findsOneWidget);
      expect(find.text('＋ New project'), findsOneWidget);
    });

    testWidgets('tapping the create tile pops ProjectPickResult.createNew',
        (tester) async {
      ProjectPickResult? popped;
      await tester.pumpWidget(_host(
        projects: projects,
        allowCreate: true,
        onResult: (r) => popped = r,
      ));
      await tester.pump();

      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const Key('project-pick-create')));
      await tester.pumpAndSettle();

      expect(popped, isNotNull);
      expect(popped!.createNew, isTrue);
      expect(popped!.category, isNull);
    });

    testWidgets('allowCreate: false (default) hides the create tile',
        (tester) async {
      await tester.pumpWidget(_host(
        projects: projects,
        onResult: (_) {},
      ));
      await tester.pump();

      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('project-pick-create')), findsNothing);
    });

    testWidgets('existing selection rows still pop the right category '
        'when allowCreate is true', (tester) async {
      ProjectPickResult? popped;
      await tester.pumpWidget(_host(
        projects: projects,
        allowCreate: true,
        current: 'Groceries',
        onResult: (r) => popped = r,
      ));
      await tester.pump();

      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const ValueKey('project-pick-p2')));
      await tester.pumpAndSettle();

      expect(popped, isNotNull);
      expect(popped!.category, 'Casa');
      expect(popped!.createNew, isFalse);

      // "No project" is unaffected too.
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('project-pick-none')));
      await tester.pumpAndSettle();
      expect(popped!.category, isNull);
      expect(popped!.createNew, isFalse);
    });
  });
}
