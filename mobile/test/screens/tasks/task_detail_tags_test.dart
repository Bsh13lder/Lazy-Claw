// D3 (widget half) — TAGS is no longer a full section with an always-on text
// field; it is a compact chip parked beside PROJECT that opens a popup.
//
// The popup must lose NOTHING the old inline field could do: add, remove,
// duplicate handling, and folding un-submitted text in on Save.

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_tags_field.dart';

import 'task_detail_harness.dart';

const _noTags = Task(
  id: 'task-1',
  userId: 'u1',
  title: 'Plain task',
  priority: 'medium',
  status: 'todo',
  owner: 'user',
  nagCount: 0,
  createdAt: '2026-06-06T00:00:00Z',
);

const _withTags = Task(
  id: 'task-1',
  userId: 'u1',
  title: 'Tagged task',
  priority: 'medium',
  status: 'todo',
  owner: 'user',
  nagCount: 0,
  createdAt: '2026-06-06T00:00:00Z',
  tags: '["work","home"]',
);

void main() {
  Future<StubTasksNotifier> open(
    WidgetTester tester, {
    Task task = _noTags,
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

  Future<void> openTagsPopup(WidgetTester tester) async {
    await tester.ensureVisible(find.byKey(kTaskTagsChipKey));
    await tester.tap(find.byKey(kTaskTagsChipKey));
    await tester.pumpAndSettle();
  }

  Future<void> save(WidgetTester tester) async {
    await tester.tap(find.byKey(const Key('task-detail-save')));
    await tester.pumpAndSettle();
  }

  List<String> savedTags(StubTasksNotifier stub) =>
      (jsonDecode(stub.updateCalls.single['tags'] as String) as List)
          .cast<String>();

  testWidgets(
    'the always-on tag field is gone from the main scroll; PROJECT and TAGS '
    'sit side by side as chips',
    (tester) async {
      await open(tester);

      expect(find.byKey(const Key('task-detail-tag-input')), findsNothing);
      expect(find.byKey(const Key('task-detail-project')), findsOneWidget);
      expect(find.byKey(kTaskTagsChipKey), findsOneWidget);
      // Empty state invites rather than reading as broken.
      expect(find.text(kTaskTagsEmptyLabel), findsOneWidget);
    },
  );

  testWidgets('an existing task summarises its tags on the chip', (
    tester,
  ) async {
    await open(tester, task: _withTags);
    expect(find.text('work +1'), findsOneWidget);
  });

  testWidgets('adding a tag in the popup round-trips into the Save payload', (
    tester,
  ) async {
    final stub = await open(tester);
    await openTagsPopup(tester);

    await tester.enterText(
      find.byKey(const Key('task-detail-tag-input')),
      'work',
    );
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pumpAndSettle();

    // The chip inside the popup appears immediately.
    expect(find.byKey(const ValueKey('task-detail-tag-work')), findsOneWidget);

    await tester.tap(find.byKey(kTaskTagsDoneKey));
    await tester.pumpAndSettle();

    // ...and the summary chip on the sheet behind it updated too.
    expect(find.text('work'), findsWidgets);

    await save(tester);
    expect(savedTags(stub), ['work']);
  });

  testWidgets('removing a tag in the popup round-trips into the Save payload', (
    tester,
  ) async {
    final stub = await open(tester, task: _withTags);
    await openTagsPopup(tester);

    await tester.tap(find.byKey(const ValueKey('task-detail-tag-work')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('task-detail-tag-work')), findsNothing);

    await tester.tap(find.byKey(kTaskTagsDoneKey));
    await tester.pumpAndSettle();
    await save(tester);

    expect(savedTags(stub), ['home']);
  });

  testWidgets('a duplicate tag is swallowed (no second chip)', (tester) async {
    final stub = await open(tester, task: _withTags);
    await openTagsPopup(tester);

    await tester.enterText(
      find.byKey(const Key('task-detail-tag-input')),
      'work',
    );
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('task-detail-tag-work')), findsOneWidget);

    await tester.tap(find.byKey(kTaskTagsDoneKey));
    await tester.pumpAndSettle();
    // Still exactly the two it opened with.
    expect(find.text('work +1'), findsOneWidget);

    await save(tester);
    // A no-op add leaves the list identical to the on-open snapshot, so Save
    // correctly writes NOTHING (the sheet's existing no-churn rule).
    expect(stub.updateCalls.single['tags'], isNull);
  });

  testWidgets(
    'text typed but never submitted is STILL folded in on Save — the field '
    'moved into a popup but the controller stays owned by the sheet',
    (tester) async {
      final stub = await open(tester);
      await openTagsPopup(tester);

      await tester.enterText(
        find.byKey(const Key('task-detail-tag-input')),
        'urgent',
      );
      // Dismiss the popup WITHOUT submitting the field.
      await tester.tap(find.byKey(kTaskTagsDoneKey));
      await tester.pumpAndSettle();

      await save(tester);
      expect(savedTags(stub), ['urgent']);
    },
  );

  testWidgets('an untouched tag list is NOT written on Save (no churn)', (
    tester,
  ) async {
    final stub = await open(tester, task: _withTags);

    await tester.enterText(
      find.byKey(const Key('task-detail-title')),
      'Edited',
    );
    await save(tester);

    expect(stub.updateCalls.single['tags'], isNull);
  });
}
