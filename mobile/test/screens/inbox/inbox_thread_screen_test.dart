import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lazyclaw_mobile/comms/inbox_models.dart';
import 'package:lazyclaw_mobile/comms/inbox_providers.dart';
import 'package:lazyclaw_mobile/comms/inbox_repository.dart';
import 'package:lazyclaw_mobile/screens/inbox/inbox_thread_screen.dart';

// ── Fake transport that records calls ─────────────────────────────────────────

class _FakeTransport implements InboxTransport {
  final List<String> markedReadIds = [];
  final List<Map<String, dynamic>> replies = [];

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async {
    if (path.endsWith('/messages')) return {'messages': []};
    return {};
  }

  @override
  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    if (path.endsWith('/read')) {
      // extract threadId from path: /api/inbox/threads/{id}/read
      final parts = path.split('/');
      final threadIdx = parts.indexOf('threads');
      if (threadIdx >= 0 && threadIdx + 1 < parts.length) {
        markedReadIds.add(parts[threadIdx + 1]);
      }
    } else if (path.endsWith('/reply')) {
      replies.add({...body, 'path': path});
    }
    return {'ok': true};
  }

  @override
  Future<List<int>> getBytes(String path) async => const <int>[];

  @override
  Future<Map<String, dynamic>> putJson(
    String path,
    Map<String, dynamic> body,
  ) async =>
      {'ok': true};

  @override
  Future<Map<String, dynamic>> deleteJson(String path) async => {'ok': true};
}

// ── Helper to build a ProviderScope with the fake transport ───────────────────

ProviderScope _buildScope({
  required String threadId,
  required String title,
  List<InboxMessage> messages = const [],
  _FakeTransport? transport,
}) {
  final t = transport ?? _FakeTransport();
  return ProviderScope(
    overrides: [
      inboxRepositoryProvider.overrideWith((_) => InboxRepository(t)),
      inboxMessagesProvider(threadId)
          .overrideWith((ref) async => messages),
    ],
    child: MaterialApp(
        home: InboxThreadScreen(threadId: threadId, title: title)),
  );
}

// ── Tests ─────────────────────────────────────────────────────────────────────

void main() {
  testWidgets('shows messages and a reply bar with mode toggle', (tester) async {
    await tester.pumpWidget(_buildScope(
      threadId: 't1',
      title: 'Alice',
      messages: const [
        InboxMessage(sender: 'Alice', text: 'are you coming?', timestamp: '10:00'),
      ],
    ));
    await tester.pumpAndSettle();

    // Message text is visible
    expect(find.text('are you coming?'), findsOneWidget);

    // Mode toggle chips are present.
    // "Send" appears twice: once as the LzChip label and once as the LzButton
    // label — both are correct; we just need at least one of each.
    expect(find.text('Send'), findsAtLeastNWidgets(1));
    expect(find.text('Ask AI'), findsOneWidget);
  });

  testWidgets('newest message renders at the BOTTOM (chat order)',
      (tester) async {
    // Channel reads return newest-first: messages[0] is the newest. With the
    // reversed list it must paint BELOW the older message, and the view opens
    // scrolled to it — like every messaging app.
    await tester.pumpWidget(_buildScope(
      threadId: 't9',
      title: 'Maria',
      messages: const [
        InboxMessage(sender: 'Maria', text: 'newest message', timestamp: '10:05'),
        InboxMessage(sender: 'Maria', text: 'older message', timestamp: '10:00'),
      ],
    ));
    await tester.pumpAndSettle();

    final newestY = tester.getCenter(find.text('newest message')).dy;
    final olderY = tester.getCenter(find.text('older message')).dy;
    expect(newestY, greaterThan(olderY),
        reason: 'newest must be below (greater dy than) the older message');
  });

  testWidgets('markRead is called on open', (tester) async {
    final transport = _FakeTransport();

    await tester.pumpWidget(ProviderScope(
      overrides: [
        inboxRepositoryProvider
            .overrideWith((_) => InboxRepository(transport)),
        inboxMessagesProvider('t2').overrideWith((ref) async => []),
      ],
      child: const MaterialApp(
          home: InboxThreadScreen(threadId: 't2', title: 'Bob')),
    ));
    await tester.pumpAndSettle();

    expect(transport.markedReadIds, contains('t2'));
  });

  testWidgets('tapping Ask AI chip switches button label to Ask', (tester) async {
    await tester.pumpWidget(_buildScope(threadId: 't3', title: 'Carol'));
    await tester.pumpAndSettle();

    // Initially in "Send" mode — the button label is "Send"
    // (chips are "Send" + "Ask AI", button is also "Send" — use last match for button)
    expect(find.text('Ask'), findsNothing);

    // Tap "Ask AI" chip
    await tester.tap(find.text('Ask AI'));
    await tester.pump();

    // The send button label changes to "Ask"
    expect(find.text('Ask'), findsOneWidget);
  });

  testWidgets('direct reply is sent on tap and field is cleared', (tester) async {
    final transport = _FakeTransport();

    await tester.pumpWidget(ProviderScope(
      overrides: [
        inboxRepositoryProvider
            .overrideWith((_) => InboxRepository(transport)),
        inboxMessagesProvider('t4').overrideWith((ref) async => []),
      ],
      child: const MaterialApp(
          home: InboxThreadScreen(threadId: 't4', title: 'Dave')),
    ));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField), 'Hello there');
    await tester.pump();

    // Tap "Send" chip-button — use widgetWithText to find the LzButton
    await tester.tap(find.widgetWithText(InkWell, 'Send').last);
    await tester.pumpAndSettle();

    expect(transport.replies.length, 1);
    expect(transport.replies.first['text'], 'Hello there');
    expect(transport.replies.first['mode'], 'direct');
    // Field is cleared after a successful send.
    expect(find.text('Hello there'), findsNothing);
  });

  testWidgets('ai reply uses mode=ai', (tester) async {
    final transport = _FakeTransport();

    await tester.pumpWidget(ProviderScope(
      overrides: [
        inboxRepositoryProvider
            .overrideWith((_) => InboxRepository(transport)),
        inboxMessagesProvider('t5').overrideWith((ref) async => []),
      ],
      child: const MaterialApp(
          home: InboxThreadScreen(threadId: 't5', title: 'Eve')),
    ));
    await tester.pumpAndSettle();

    // Switch to AI mode
    await tester.tap(find.text('Ask AI'));
    await tester.pump();

    await tester.enterText(find.byType(TextField), 'Say something nice');
    await tester.pump();

    // Tap "Ask" button
    await tester.tap(find.widgetWithText(InkWell, 'Ask').last);
    await tester.pumpAndSettle();

    expect(transport.replies.length, 1);
    expect(transport.replies.first['mode'], 'ai');
  });

  testWidgets('shows sender label on received bubble, not on mine', (tester) async {
    await tester.pumpWidget(_buildScope(
      threadId: 't6',
      title: 'Alice',
      messages: const [
        InboxMessage(
          sender: 'Alice',
          text: 'hello from Alice',
          timestamp: '09:00',
          isMine: false,
        ),
        InboxMessage(
          sender: 'Me',
          text: 'hello from me',
          timestamp: '09:01',
          isMine: true,
        ),
      ],
    ));
    await tester.pumpAndSettle();

    // Sender label shown for received messages (in app bar title + bubble label)
    expect(find.text('Alice'), findsAtLeastNWidgets(1));
    expect(find.text('hello from Alice'), findsOneWidget);
    expect(find.text('hello from me'), findsOneWidget);
    // Own (mine) bubble must NOT show a sender caption.
    expect(find.text('Me'), findsNothing);
  });
}
