import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/repositories/chat_history_repository.dart';

// ── Fake transport ─────────────────────────────────────────────────────────

/// Returns a queued response per path prefix so a single repo call (which hits
/// `/sessions` then `/sessions/{id}/messages`) can be driven deterministically.
class _FakeChatTransport implements ChatHistoryTransport {
  final Map<String, dynamic> sessionsResponse;
  final Map<String, dynamic> messagesResponse;
  final List<String> calls = [];

  _FakeChatTransport({
    this.sessionsResponse = const {'sessions': []},
    this.messagesResponse = const {'messages': []},
  });

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async {
    calls.add(path);
    if (path.contains('/messages')) return messagesResponse;
    return sessionsResponse;
  }
}

// ── Tests ──────────────────────────────────────────────────────────────────

void main() {
  group('mapApiMessage', () {
    test('maps a user row to a user bubble', () {
      final m = mapApiMessage({'role': 'user', 'content': 'hello'});
      expect(m, isNotNull);
      expect(m!.role, 'user');
      expect(m.content, 'hello');
    });

    test('maps an assistant row, never streaming', () {
      final m = mapApiMessage({'role': 'assistant', 'content': 'hi there'});
      expect(m!.role, 'assistant');
      expect(m.streaming, isFalse);
    });

    test('turns tool_calls into done ToolActivity chips (result → preview)', () {
      final m = mapApiMessage({
        'role': 'assistant',
        'content': 'searched',
        'tool_calls': [
          {
            'name': 'web_search',
            'args': {'q': 'x'},
            'result': 'found 3',
          },
        ],
      });
      expect(m!.toolActivities, hasLength(1));
      expect(m.toolActivities.first.name, 'web_search');
      expect(m.toolActivities.first.resultPreview, 'found 3');
    });

    test('drops empty assistant rows with no text and no tools', () {
      expect(mapApiMessage({'role': 'assistant', 'content': ''}), isNull);
    });

    test('drops bare tool / system rows', () {
      expect(mapApiMessage({'role': 'tool', 'content': 'output'}), isNull);
      expect(mapApiMessage({'role': 'system', 'content': 'x'}), isNull);
    });
  });

  group('ChatHistoryRepository.loadPrimaryHistory', () {
    test('returns empty when there are no sessions', () async {
      final repo = ChatHistoryRepository(_FakeChatTransport());
      expect(await repo.loadPrimaryHistory(), isEmpty);
    });

    test('picks the primary session and maps its messages oldest-first',
        () async {
      final t = _FakeChatTransport(
        sessionsResponse: {
          'sessions': [
            {'id': 's-branch', 'title': 'Branch', 'is_primary': false},
            {'id': 's-main', 'title': 'Main', 'is_primary': true},
          ],
        },
        messagesResponse: {
          'messages': [
            {'role': 'user', 'content': 'first'},
            {'role': 'assistant', 'content': 'second'},
          ],
        },
      );
      final repo = ChatHistoryRepository(t);
      final history = await repo.loadPrimaryHistory();

      // Hit the primary session id, not the branch.
      expect(t.calls.last, contains('s-main'));
      expect(history, hasLength(2));
      expect(history[0].content, 'first');
      expect(history[1].content, 'second');
    });

    test('falls back to the first session when none is flagged primary',
        () async {
      final t = _FakeChatTransport(
        sessionsResponse: {
          'sessions': [
            {'id': 's-only', 'title': 'Only', 'is_primary': false},
          ],
        },
        messagesResponse: {
          'messages': [
            {'role': 'user', 'content': 'hi'},
          ],
        },
      );
      final history = await ChatHistoryRepository(t).loadPrimaryHistory();
      expect(t.calls.last, contains('s-only'));
      expect(history, hasLength(1));
    });

    test('filters out unrenderable rows from the mapped history', () async {
      final t = _FakeChatTransport(
        sessionsResponse: {
          'sessions': [
            {'id': 's1', 'is_primary': true},
          ],
        },
        messagesResponse: {
          'messages': [
            {'role': 'user', 'content': 'q'},
            {'role': 'tool', 'content': 'raw tool output'},
            {'role': 'assistant', 'content': 'a'},
          ],
        },
      );
      final history = await ChatHistoryRepository(t).loadPrimaryHistory();
      expect(history, hasLength(2)); // tool row dropped
      expect(history.map((m) => m.role), ['user', 'assistant']);
    });
  });

  group('ChatSessionInfo.fromJson', () {
    test('treats is_primary as both bool true and int 1', () {
      expect(ChatSessionInfo.fromJson({'id': 'a', 'is_primary': true}).isPrimary,
          isTrue);
      expect(ChatSessionInfo.fromJson({'id': 'b', 'is_primary': 1}).isPrimary,
          isTrue);
      expect(ChatSessionInfo.fromJson({'id': 'c', 'is_primary': false}).isPrimary,
          isFalse);
    });
  });
}
