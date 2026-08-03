// Widget tests for the AI badge that marks agent-created task rows.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/screens/tasks/ai_task_badge.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

Task _task(String id, {String owner = 'user'}) => Task(
  id: id,
  userId: 'u1',
  title: 'Task $id',
  priority: 'medium',
  status: 'todo',
  owner: owner,
  nagCount: 0,
  createdAt: '2026-06-06T00:00:00Z',
);

Widget _host(Widget child) => MaterialApp(
  theme: buildAppTheme(),
  home: Scaffold(body: child),
);

void main() {
  testWidgets('AgentTaskBadged prepends an AI badge for agent tasks', (
    tester,
  ) async {
    await tester.pumpWidget(
      _host(
        AgentTaskBadged(
          task: _task('a', owner: 'agent'),
          child: const Text('row body'),
        ),
      ),
    );
    await tester.pump();

    expect(find.byType(AiTaskBadge), findsOneWidget);
    expect(find.text('AI'), findsOneWidget);
    expect(find.text('row body'), findsOneWidget);
  });

  testWidgets('AgentTaskBadged renders the child verbatim for user tasks', (
    tester,
  ) async {
    await tester.pumpWidget(
      _host(
        AgentTaskBadged(
          task: _task('a', owner: 'user'),
          child: const Text('row body'),
        ),
      ),
    );
    await tester.pump();

    expect(find.byType(AiTaskBadge), findsNothing);
    expect(find.text('AI'), findsNothing);
    expect(find.text('row body'), findsOneWidget);
  });
}
