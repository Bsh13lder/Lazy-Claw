// Widget tests for the comment thread + composer.
//
// Provider-free by design: showCommentsSheet takes plain callbacks
// (onAdd/onDelete/onAddLink), so these tests drive it from a bare MaterialApp
// host — no ProviderScope, no DAO/DB.
//
// The standalone `TaskCommentsSection` widget these tests used to mount was
// deleted with D4 (2026-08-03): task-level comments moved OUT of an inline
// section at the bottom of the detail sheet and into this same popup, so
// there is exactly one comments surface again. The body under test is
// unchanged — only the way it is presented moved.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/comment.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_comments_section.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';
import 'package:lazyclaw_mobile/widgets/link_text.dart';

void main() {
  /// Mounts the comments body directly (no modal route) so these tests stay
  /// about the thread + composer rather than about sheet presentation, which
  /// the `showCommentsSheet` group below covers.
  Widget host({
    required List<TaskComment> comments,
    required ValueChanged<String> onAdd,
    required ValueChanged<String> onDelete,
    Future<String?> Function()? onAddLink,
  }) {
    return MaterialApp(
      theme: buildAppTheme(),
      home: Scaffold(
        body: Builder(
          builder: (ctx) => ElevatedButton(
            onPressed: () => showCommentsSheet(
              ctx,
              title: 'Comments',
              comments: comments,
              onAdd: (text) async {
                onAdd(text);
                return null;
              },
              onDelete: onDelete,
              onAddLink: onAddLink,
            ),
            child: const Text('open'),
          ),
        ),
      ),
    );
  }

  /// Every test in this file starts by opening the sheet.
  Future<void> pumpOpen(WidgetTester tester, Widget app) async {
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(800, 1600);
    addTearDown(tester.view.reset);
    await tester.pumpWidget(app);
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
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
    await pumpOpen(
      tester,
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

  test(
    'taskLevelComments drops a sub-task comment — the sheet itself no longer '
    'filters, the CALLER decides scope (see showCommentsSheet)',
    () {
      const subtaskComment = TaskComment(
        id: 'c-3',
        ts: '2026-08-01T12:00:00Z',
        author: 'user',
        text: 'subtask-only note',
        subtaskId: 'sub-1',
      );

      final kept = taskLevelComments(const [userComment, subtaskComment]);
      expect(kept.map((c) => c.id), ['c-1']);
    },
  );

  testWidgets('submitting the input fires onAdd and clears the field', (
    tester,
  ) async {
    String? captured;
    await pumpOpen(
      tester,
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

  testWidgets('typing beyond 2000 chars is capped by the field itself', (
    tester,
  ) async {
    await pumpOpen(
      tester,
      host(comments: const [], onAdd: (_) {}, onDelete: (_) {}),
    );

    final over = 'a' * 2100;
    await tester.enterText(find.byKey(const Key('comment-input')), over);
    await tester.pump();

    final field = tester.widget<TextField>(
      find.byKey(const Key('comment-input')),
    );
    expect(field.controller!.text.length, 2000);
    expect(field.controller!.text, 'a' * 2000);
  });

  testWidgets(
    'the maxLength counter ("n / 2000") is hidden — enforcement stays but '
    'the default counter text never renders (visual noise in a tight Row)',
    (tester) async {
      await pumpOpen(
        tester,
        host(comments: const [], onAdd: (_) {}, onDelete: (_) {}),
      );

      // Nothing typed yet — a visible default counter would already read
      // "0/2000" at this point.
      expect(find.text('0/2000'), findsNothing);

      await tester.enterText(find.byKey(const Key('comment-input')), 'hello');
      await tester.pump();

      // Still capped/enforced (see the 2100-char test above)...
      final field = tester.widget<TextField>(
        find.byKey(const Key('comment-input')),
      );
      expect(field.maxLength, kMaxCommentChars);
      expect(field.buildCounter, isNotNull);
      expect(
        field.buildCounter!(
          tester.element(find.byKey(const Key('comment-input'))),
          currentLength: 5,
          maxLength: kMaxCommentChars,
          isFocused: false,
        ),
        isNull,
      );
      // ...but no counter text is rendered anywhere in the tree.
      expect(find.text('5/2000'), findsNothing);
    },
  );

  testWidgets('an empty submit is a no-op', (tester) async {
    var calls = 0;
    await pumpOpen(
      tester,
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
    await pumpOpen(
      tester,
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
    await pumpOpen(
      tester,
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
    await pumpOpen(
      tester,
      host(comments: const [], onAdd: (_) {}, onDelete: (_) {}),
    );
    expect(find.byKey(const Key('comment-add-link')), findsNothing);

    // Dismiss the first sheet before opening the second — pumping a new host
    // over a live modal route would leave both composers in the tree.
    await tester.tapAt(const Offset(20, 20));
    await tester.pumpAndSettle();

    await pumpOpen(
      tester,
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
    await pumpOpen(
      tester,
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

  testWidgets(
    'a non-overflowing insert splices at the cursor position, not the end',
    (tester) async {
      await pumpOpen(
        tester,
        host(
          comments: const [],
          onAdd: (_) {},
          onDelete: (_) {},
          onAddLink: () async => '[x](y)',
        ),
      );

      await tester.enterText(
        find.byKey(const Key('comment-input')),
        'before after',
      );
      await tester.pump();

      final field = tester.widget<TextField>(
        find.byKey(const Key('comment-input')),
      );
      // Place the cursor right after "before" (offset 6), mid-string.
      field.controller!.selection = const TextSelection.collapsed(offset: 6);
      await tester.pump();

      await tester.tap(find.byKey(const Key('comment-add-link')));
      await tester.pumpAndSettle();

      final updated = tester.widget<TextField>(
        find.byKey(const Key('comment-input')),
      );
      expect(updated.controller!.text, 'before[x](y) after');
    },
  );

  testWidgets(
    'an overflowing splice is clamped: field text unchanged + snackbar shown',
    (tester) async {
      await pumpOpen(
        tester,
        host(
          comments: const [],
          onAdd: (_) {},
          onDelete: (_) {},
          onAddLink: () async => 'x' * 50,
        ),
      );

      final seed = 'a' * 1990;
      await tester.enterText(find.byKey(const Key('comment-input')), seed);
      await tester.pump();

      await tester.tap(find.byKey(const Key('comment-add-link')));
      await tester.pumpAndSettle();

      final field = tester.widget<TextField>(
        find.byKey(const Key('comment-input')),
      );
      expect(field.controller!.text, seed);
      expect(find.text('Comment limit is 2000 characters.'), findsOneWidget);
    },
  );

  group('showCommentsSheet (sub-task scope)', () {
    // Regression test for the add-then-delete-in-same-session bug: the sheet
    // used to synthesize its OWN local id for an optimistically-added comment
    // instead of using the id the real persistence layer actually minted. A
    // delete fired right after (before ever reopening the sheet) would then
    // target that fake local id, which no persisted comment has — a silent
    // no-op against the real store that only "looked" like it worked because
    // the local list was filtered anyway. Fixed by having `onAdd` return the
    // real persisted TaskComment (mirrors TasksNotifier.addComment's new
    // Future<TaskComment?> return) and appending THAT to the local list.
    Widget sheetHost({
      required Future<TaskComment?> Function(String) onAdd,
      required ValueChanged<String> onDelete,
      List<TaskComment> comments = const [],
      Future<String?> Function()? onAddLink,
    }) {
      return MaterialApp(
        theme: buildAppTheme(),
        home: Scaffold(
          body: Builder(
            builder: (ctx) => ElevatedButton(
              onPressed: () => showCommentsSheet(
                ctx,
                title: 'Draft outline',
                comments: comments,
                onAdd: onAdd,
                onDelete: onDelete,
                onAddLink: onAddLink,
              ),
              child: const Text('open'),
            ),
          ),
        ),
      );
    }

    testWidgets('the sheet body built WITH onAddLink shows the add-link icon', (
      tester,
    ) async {
      await tester.pumpWidget(
        sheetHost(
          onAdd: (_) async => null,
          onDelete: (_) {},
          onAddLink: () async => '[docs](https://a.io)',
        ),
      );

      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();

      expect(find.byIcon(Icons.add_link), findsOneWidget);
    });

    testWidgets(
      'add then delete in the same session deletes the PERSISTED id, not a '
      'locally-synthesized one',
      (tester) async {
        final deletedIds = <String>[];
        var addedText = '';

        await tester.pumpWidget(
          sheetHost(
            onAdd: (text) async {
              addedText = text;
              // Simulate TasksNotifier.addComment: mints its OWN id, which a
              // locally-synthesized guess could never predict.
              return const TaskComment(
                id: 'server-minted-42',
                ts: '2026-08-02T09:00:00Z',
                author: 'user',
                text: 'new note',
                subtaskId: 'sub-1',
              );
            },
            onDelete: (id) => deletedIds.add(id),
          ),
        );

        await tester.tap(find.text('open'));
        await tester.pumpAndSettle();

        await tester.enterText(
          find.byKey(const Key('comment-input')),
          'new note',
        );
        await tester.tap(find.byKey(const Key('comment-send')));
        await tester.pumpAndSettle();

        expect(addedText, 'new note');
        // The appended comment carries the REAL (server-minted) id.
        expect(
          find.byKey(const ValueKey('comment-server-minted-42')),
          findsOneWidget,
        );

        await tester.longPress(
          find.byKey(const ValueKey('comment-server-minted-42')),
        );
        await tester.pumpAndSettle();
        await tester.tap(find.text('Delete'));
        await tester.pumpAndSettle();

        // onDelete must fire with the SAME id the "server" minted — not a
        // locally-synthesized guess that no persisted comment has.
        expect(deletedIds, ['server-minted-42']);
        expect(
          find.byKey(const ValueKey('comment-server-minted-42')),
          findsNothing,
        );
      },
    );

    testWidgets('a failed add (onAdd resolves null) does not append locally', (
      tester,
    ) async {
      final deletedIds = <String>[];
      await tester.pumpWidget(
        sheetHost(
          onAdd: (text) async => null,
          onDelete: (id) => deletedIds.add(id),
        ),
      );

      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();

      await tester.enterText(find.byKey(const Key('comment-input')), 'ignored');
      await tester.tap(find.byKey(const Key('comment-send')));
      await tester.pumpAndSettle();

      expect(
        find.byWidgetPredicate((w) => w is LinkText && w.text == 'ignored'),
        findsNothing,
      );
    });
  });
}
