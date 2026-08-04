// Comments already carried a relative timestamp; this file pins that it still
// works AFTER the private per-screen `_relativeTime` was replaced by the one
// shared `formatTimestampLabel` used by tasks and sub-tasks.
//
// The behaviour change worth guarding is the OLD branch: the private helper
// fell back to a raw `d/m/yyyy`, which is a second date vocabulary nobody else
// in the app speaks (and is ambiguous besides). It now reads as a real date.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/comment.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_comments_section.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

Widget _host(List<TaskComment> comments) => MaterialApp(
  theme: buildAppTheme(),
  home: Scaffold(
    body: Builder(
      builder: (ctx) => ElevatedButton(
        onPressed: () => showCommentsSheet(
          ctx,
          title: 'Comments',
          comments: comments,
          onAdd: (_) async => null,
          onDelete: (_) {},
        ),
        child: const Text('open'),
      ),
    ),
  ),
);

Future<void> _open(WidgetTester tester, List<TaskComment> comments) async {
  tester.view.devicePixelRatio = 1.0;
  tester.view.physicalSize = const Size(800, 1600);
  addTearDown(tester.view.reset);
  await tester.pumpWidget(_host(comments));
  await tester.tap(find.text('open'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('a fresh comment still reads "just now"', (tester) async {
    await _open(tester, [
      TaskComment(
        id: 'c1',
        ts: DateTime.now().toUtc().toIso8601String(),
        author: 'user',
        text: 'hello',
      ),
    ]);

    expect(find.text('just now'), findsOneWidget);
  });

  testWidgets('an old comment reads as a real date, not "d/m/yyyy"', (
    tester,
  ) async {
    await _open(tester, const [
      TaskComment(
        id: 'c1',
        ts: '2024-01-15T10:00:00Z',
        author: 'agent',
        text: 'ancient',
      ),
    ]);

    expect(find.textContaining('Jan'), findsOneWidget);
    expect(find.textContaining('2024'), findsOneWidget);
    expect(find.textContaining('/'), findsNothing);
  });

  testWidgets('an unparseable ts renders an empty label, never a crash', (
    tester,
  ) async {
    await _open(tester, const [
      TaskComment(id: 'c1', ts: 'nonsense', author: 'user', text: 'still here'),
    ]);

    expect(find.text('still here'), findsOneWidget);
    expect(find.textContaining('nonsense'), findsNothing);
  });
}
