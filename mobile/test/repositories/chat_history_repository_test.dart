import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/chat/chat_message.dart';
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

    // Real server contract (2026-08): tool_calls entries are
    // {id, name, display, arguments, result?, status} with
    // status ∈ {"done","unknown"} and result ≤500 chars when present.
    test('maps real-contract tool_calls entries onto settled chips', () {
      final m = mapApiMessage({
        'role': 'assistant',
        'content': 'searched',
        'tool_calls': [
          {
            'id': 'tc-1',
            'name': 'web_search',
            'display': 'Searching the web',
            'arguments': {'query': 'x'},
            'result': 'found 3',
            'status': 'done',
          },
          {
            'id': 'tc-2',
            'name': 'browser',
            'display': 'Browsing',
            'arguments': {'url': 'https://example.com'},
            'status': 'unknown',
          },
        ],
      });
      expect(m!.toolActivities, hasLength(2));

      final done = m.toolActivities[0];
      expect(done.name, 'web_search');
      expect(done.displayName, 'Searching the web');
      expect(done.toolCallId, 'tc-1');
      expect(done.args, {'query': 'x'});
      expect(done.resultPreview, 'found 3');
      expect(done.status, ToolStatus.done);

      final unknown = m.toolActivities[1];
      expect(unknown.status, ToolStatus.unknown);
      expect(unknown.resultPreview, isNull);
      expect(unknown.args, {'url': 'https://example.com'});
    });

    test('history chips NEVER spin — any non-done status maps to unknown', () {
      final m = mapApiMessage({
        'role': 'assistant',
        'content': 'x',
        'tool_calls': [
          // Even a bogus/hostile "running" from the wire must not spin.
          {'name': 'browser', 'arguments': {}, 'status': 'running'},
          {'name': 'web_search', 'arguments': {}},
          {'name': 'recall_memories', 'arguments': {}, 'status': 'weird'},
        ],
      });
      for (final t in m!.toolActivities) {
        expect(t.status, isNot(ToolStatus.running),
            reason: 'history mapping must never produce a running chip');
      }
    });

    test('legacy args key still maps; result-without-status counts as done',
        () {
      final m = mapApiMessage({
        'role': 'assistant',
        'content': 'x',
        'tool_calls': [
          {
            'name': 'web_search',
            'args': {'q': 'x'},
            'result': 'found 3',
          },
        ],
      });
      final t = m!.toolActivities.single;
      expect(t.args, {'q': 'x'}, reason: 'old servers send args, not arguments');
      expect(t.resultPreview, 'found 3');
      expect(t.status, ToolStatus.done,
          reason: 'a carried result implies the call finished');
    });

    test('user rows carry kind == cron for scheduled-job rows', () {
      final m = mapApiMessage({
        'role': 'user',
        'content': '[JOB:daily-briefing] Run the morning briefing',
        'id': 'u1',
        'kind': 'cron',
      });
      expect(m!.kind, 'cron');
      expect(m.content, '[JOB:daily-briefing] Run the morning briefing');

      final plain = mapApiMessage({'role': 'user', 'content': 'hi'});
      expect(plain!.kind, isNull, reason: 'no regression pre-server-deploy');
    });

    test('drops empty assistant rows with no text and no tools', () {
      expect(mapApiMessage({'role': 'assistant', 'content': ''}), isNull);
    });

    test('drops bare tool / system rows', () {
      expect(mapApiMessage({'role': 'tool', 'content': 'output'}), isNull);
      expect(mapApiMessage({'role': 'system', 'content': 'x'}), isNull);
    });

    test('carries the server row id (stringified) on user and assistant rows',
        () {
      final u = mapApiMessage({'role': 'user', 'content': 'q', 'id': 17});
      expect(u!.id, '17');
      final a =
          mapApiMessage({'role': 'assistant', 'content': 'a', 'id': 'msg-9'});
      expect(a!.id, 'msg-9');
    });

    test('carries kind == notification; rows without kind stay null', () {
      final n = mapApiMessage({
        'role': 'assistant',
        'content': 'Reminder\nCall Buchvardi',
        'id': 'n1',
        'kind': 'notification',
      });
      expect(n!.kind, 'notification');
      expect(n.content, 'Reminder\nCall Buchvardi');

      final plain = mapApiMessage({'role': 'assistant', 'content': 'hi'});
      expect(plain!.kind, isNull, reason: 'graceful when the field is absent');
      expect(plain.id, isNull);
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

    test('collapses a batch-persisted turn into ONE bubble (both the seed and '
        'the delta-merge tail come through here)', () async {
      final t = _FakeChatTransport(
        sessionsResponse: {
          'sessions': [
            {'id': 's1', 'is_primary': true},
          ],
        },
        messagesResponse: {
          'messages': [
            {
              'role': 'user',
              'content': 'what does James want?',
              'id': 'u1',
              'created_at': '2026-08-13 15:31:05',
            },
            // Interim status row — carries the turn's tool_calls metadata.
            {
              'role': 'assistant',
              'content': 'Checking the Upwork thread now…',
              'id': 'a1',
              'created_at': '2026-08-13 15:31:09',
              'tool_calls': [
                {
                  'id': 'c1',
                  'name': 'upwork_last_conversation',
                  'status': 'done',
                  'result': 'ok',
                },
              ],
            },
            // Final reply of the SAME turn (same created_at).
            {
              'role': 'assistant',
              'content': 'He narrowed the scope to 6 cities.',
              'id': 'a2',
              'created_at': '2026-08-13 15:31:09',
            },
          ],
        },
      );
      final history = await ChatHistoryRepository(t).loadPrimaryHistory();

      expect(history, hasLength(2), reason: 'one user row + one merged turn');
      final turn = history.last;
      expect(turn.content, 'He narrowed the scope to 6 cities.');
      expect(turn.id, 'a2');
      expect(turn.absorbedIds, ['a1']);
      expect(turn.toolActivities.single.name, 'upwork_last_conversation');
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
