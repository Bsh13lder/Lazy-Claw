// Widget tests for the task-level comment thread + composer.
//
// Provider-free by design (per the task brief): TaskCommentsSection takes
// plain callbacks (onAdd/onDelete/onAddLink), so these tests exercise it
// directly with a bare MaterialApp host — no ProviderScope, no DAO/DB.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/comment.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_comments_section.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:lazyclaw_mobile/widgets/link_text.dart';

void main() {
  Widget host({
    required List<TaskComment> comments,
    required ValueChanged<String> onAdd,
    required ValueChanged<String> onDelete,
    Future<String?> Function()? onAddLink,
  }) {
    return MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        body: TaskCommentsSection(
          comments: comments,
          onAdd: onAdd,
          onDelete: onDelete,
          onAddLink: onAddLink,
        ),
      ),
    );
  }

  const userComment = TaskComment(
    id: 'c-1',
    ts: '2026-08-01T10:00:00Z',
    author: 'user',
    text: 'looks good to me',
  );

  const agentComment = TaskComment(
    id: 'c-2',
    ts: '2026-08-01T11:00:00Z',
    author: 'agent',
    text: 'done, see https://a.io/report',
  );

  testWidgets('renders author labels and text through LinkText', (
    tester,
  ) async {
    await tester.pumpWidget(
      host(
        comments: const [userComment, agentComment],
        onAdd: (_) {},
        onDelete: (_) {},
      ),
    );

    expect(find.text('You'), findsOneWidget);
    expect(find.text('Lazy 🤖'), findsOneWidget);
    expect(
      find.byWidgetPredicate(
        (w) => w is LinkText && w.text == 'looks good to me',
      ),
      findsOneWidget,
    );
    expect(
      find.byWidgetPredicate(
        (w) => w is LinkText && w.text == 'done, see https://a.io/report',
      ),
      findsOneWidget,
    );
  });

  testWidgets(
    'a comment with subtaskId set does NOT render in the task-level section',
    (tester) async {
      const subtaskComment = TaskComment(
        id: 'c-3',
        ts: '2026-08-01T12:00:00Z',
        author: 'user',
        text: 'subtask-only note',
        subtaskId: 'sub-1',
      );

      await tester.pumpWidget(
        host(
          comments: const [userComment, subtaskComment],
          onAdd: (_) {},
          onDelete: (_) {},
        ),
      );

      expect(
        find.byWidgetPredicate(
          (w) => w is LinkText && w.text == 'looks good to me',
        ),
        findsOneWidget,
      );
      expect(
        find.byWidgetPredicate(
          (w) => w is LinkText && w.text == 'subtask-only note',
        ),
        findsNothing,
      );
      expect(find.byKey(const ValueKey('comment-c-3')), findsNothing);
    },
  );

  testWidgets('submitting the input fires onAdd and clears the field', (
    tester,
  ) async {
    String? captured;
    await tester.pumpWidget(
      host(
        comments: const [],
        onAdd: (text) => captured = text,
        onDelete: (_) {},
      ),
    );

    await tester.enterText(
      find.byKey(const Key('comment-input')),
      'typed text',
    );
    await tester.tap(find.byKey(const Key('comment-send')));
    await tester.pump();

    expect(captured, 'typed text');
    final field = tester.widget<TextField>(
      find.byKey(const Key('comment-input')),
    );
    expect(field.controller!.text, isEmpty);
  });

  testWidgets('an empty submit is a no-op', (tester) async {
    var calls = 0;
    await tester.pumpWidget(
      host(comments: const [], onAdd: (_) => calls++, onDelete: (_) {}),
    );

    await tester.tap(find.byKey(const Key('comment-send')));
    await tester.pump();

    expect(calls, 0);
  });

  testWidgets('long-press a comment, confirm, invokes onDelete(id)', (
    tester,
  ) async {
    String? deletedId;
    await tester.pumpWidget(
      host(
        comments: const [userComment],
        onAdd: (_) {},
        onDelete: (id) => deletedId = id,
      ),
    );

    await tester.longPress(find.byKey(const ValueKey('comment-c-1')));
    await tester.pumpAndSettle();

    // Confirmation dialog is up; nothing deleted yet.
    expect(deletedId, isNull);
    expect(find.text('Delete comment?'), findsOneWidget);

    await tester.tap(find.text('Delete'));
    await tester.pumpAndSettle();

    expect(deletedId, 'c-1');
  });

  testWidgets('long-press then Cancel does NOT invoke onDelete', (
    tester,
  ) async {
    String? deletedId;
    await tester.pumpWidget(
      host(
        comments: const [userComment],
        onAdd: (_) {},
        onDelete: (id) => deletedId = id,
      ),
    );

    await tester.longPress(find.byKey(const ValueKey('comment-c-1')));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();

    expect(deletedId, isNull);
  });

  testWidgets('the add-link icon only renders when onAddLink is supplied', (
    tester,
  ) async {
    await tester.pumpWidget(
      host(comments: const [], onAdd: (_) {}, onDelete: (_) {}),
    );
    expect(find.byKey(const Key('comment-add-link')), findsNothing);

    await tester.pumpWidget(
      host(
        comments: const [],
        onAdd: (_) {},
        onDelete: (_) {},
        onAddLink: () async => '[docs](https://a.io)',
      ),
    );
    expect(find.byKey(const Key('comment-add-link')), findsOneWidget);
  });

  testWidgets('the add-link icon inserts the dialog result into the field', (
    tester,
  ) async {
    await tester.pumpWidget(
      host(
        comments: const [],
        onAdd: (_) {},
        onDelete: (_) {},
        onAddLink: () async => '[docs](https://a.io)',
      ),
    );

    await tester.tap(find.byKey(const Key('comment-add-link')));
    await tester.pumpAndSettle();

    final field = tester.widget<TextField>(
      find.byKey(const Key('comment-input')),
    );
    expect(field.controller!.text, '[docs](https://a.io)');
  });
}
