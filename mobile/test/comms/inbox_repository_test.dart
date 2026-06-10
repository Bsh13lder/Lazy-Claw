import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/comms/inbox_models.dart';
import 'package:lazyclaw_mobile/comms/inbox_repository.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';

// ── Fake transport ────────────────────────────────────────────────────────────

class _FakeTransport implements InboxTransport {
  String? lastPath;
  Map<String, dynamic>? lastBody;
  Map<String, dynamic>? lastQueryParams;
  String? lastMethod;
  Map<String, dynamic> response;

  _FakeTransport(this.response);

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async {
    lastMethod = 'GET';
    lastPath = path;
    lastQueryParams = queryParams;
    return response;
  }

  @override
  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    lastMethod = 'POST';
    lastPath = path;
    lastBody = body;
    return response;
  }

  List<int> bytesResponse = const <int>[];

  @override
  Future<List<int>> getBytes(String path) async {
    lastMethod = 'GET-BYTES';
    lastPath = path;
    return bytesResponse;
  }

  @override
  Future<Map<String, dynamic>> putJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    lastMethod = 'PUT';
    lastPath = path;
    lastBody = body;
    return response;
  }

  @override
  Future<Map<String, dynamic>> deleteJson(String path) async {
    lastMethod = 'DELETE';
    lastPath = path;
    return response;
  }
}

/// Fake transport that always throws the production error shape:
/// [ApiError] is what [DioInboxTransport] propagates when the
/// [_ErrorInterceptor] rejects a non-2xx response.
class _ErrorTransport implements InboxTransport {
  final ApiError error;
  _ErrorTransport(this.error);

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) =>
      Future.error(error);

  @override
  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  ) =>
      Future.error(error);

  @override
  Future<List<int>> getBytes(String path) => Future.error(error);

  @override
  Future<Map<String, dynamic>> putJson(
    String path,
    Map<String, dynamic> body,
  ) =>
      Future.error(error);

  @override
  Future<Map<String, dynamic>> deleteJson(String path) => Future.error(error);
}

// ── Tests ─────────────────────────────────────────────────────────────────────

void main() {
  // ── Model tests ────────────────────────────────────────────────────────────

  test('InboxThread.fromJson parses server shape', () {
    final t = InboxThread.fromJson({
      'id': 't1',
      'channel': 'whatsapp',
      'contact_handle': '+34600000000',
      'contact_name': 'Alice',
      'last_preview': 'hi',
      'unread_count': 2,
      'last_activity': '2026-06-09T10:00:00Z',
      'updated_at': '2026-06-09T10:00:00Z',
    });
    expect(t.id, 't1');
    expect(t.channel, 'whatsapp');
    expect(t.contactHandle, '+34600000000');
    expect(t.contactName, 'Alice');
    expect(t.lastPreview, 'hi');
    expect(t.unreadCount, 2);
    expect(t.lastActivity, '2026-06-09T10:00:00Z');
  });

  test('InboxThread.fromJson uses defaults for missing optional fields', () {
    final t = InboxThread.fromJson({
      'id': 't2',
      'channel': 'telegram',
      'last_activity': '2026-06-09T11:00:00Z',
      'updated_at': '2026-06-09T11:00:00Z',
    });
    expect(t.contactHandle, '');
    expect(t.contactName, isNull);
    expect(t.lastPreview, isNull);
    expect(t.unreadCount, 0);
  });

  test('InboxMessage.fromJson parses', () {
    final m = InboxMessage.fromJson({
      'sender': 'Bob',
      'text': 'yo',
      'timestamp': '10:00',
      'is_mine': false,
    });
    expect(m.sender, 'Bob');
    expect(m.text, 'yo');
    expect(m.timestamp, '10:00');
    expect(m.isMine, false);
  });

  test('InboxMessage.fromJson parses is_mine=true', () {
    final m = InboxMessage.fromJson({
      'sender': 'me',
      'text': 'hello',
      'timestamp': '10:01',
      'is_mine': true,
    });
    expect(m.isMine, true);
  });

  test('InboxMessage.fromJson handles missing optional fields', () {
    final m = InboxMessage.fromJson({});
    expect(m.sender, '');
    expect(m.text, '');
    expect(m.timestamp, '');
    expect(m.isMine, false);
    expect(m.id, isNull);
    expect(m.media, isNull);
  });

  test('InboxMessage.fromJson parses id + media (voice note)', () {
    final m = InboxMessage.fromJson({
      'sender': 'Maria',
      'text': '[audio]',
      'timestamp': '10:00',
      'is_mine': false,
      'id': 'MSG1',
      'media': {
        'kind': 'audio',
        'mimetype': 'audio/ogg; codecs=opus',
        'voice_note': true,
        'seconds': 14,
        'file_name': null,
        'size_bytes': 9001,
      },
    });
    expect(m.id, 'MSG1');
    expect(m.media, isNotNull);
    expect(m.media!.kind, 'audio');
    expect(m.media!.voiceNote, true);
    expect(m.media!.seconds, 14);
    expect(m.media!.sizeBytes, 9001);
  });

  test('InboxMedia.fromJson defaults for malformed payload', () {
    final media = InboxMedia.fromJson({});
    expect(media.kind, 'document');
    expect(media.mimetype, 'application/octet-stream');
    expect(media.voiceNote, false);
    expect(media.fileName, isNull);
  });

  // ── Repository tests ───────────────────────────────────────────────────────

  group('InboxRepository.fetchThreads', () {
    test('calls GET /api/inbox/threads and parses result', () async {
      final transport = _FakeTransport({
        'threads': [
          {
            'id': 't1',
            'channel': 'whatsapp',
            'contact_handle': '+34600000000',
            'contact_name': 'Alice',
            'last_preview': 'hi',
            'unread_count': 2,
            'last_activity': '2026-06-09T10:00:00Z',
            'updated_at': '2026-06-09T10:00:00Z',
          }
        ],
        'count': 1,
      });
      final repo = InboxRepository(transport);
      final threads = await repo.fetchThreads();
      expect(transport.lastMethod, 'GET');
      expect(transport.lastPath, '/api/inbox/threads');
      expect(transport.lastQueryParams, isNull);
      expect(threads.length, 1);
      expect(threads.first.contactName, 'Alice');
    });

    test('passes channel filter as query param', () async {
      final transport = _FakeTransport({'threads': [], 'count': 0});
      final repo = InboxRepository(transport);
      await repo.fetchThreads(channel: 'telegram');
      expect(transport.lastQueryParams, {'channel': 'telegram'});
    });

    test('handles empty threads list', () async {
      final transport = _FakeTransport({'threads': [], 'count': 0});
      final repo = InboxRepository(transport);
      final threads = await repo.fetchThreads();
      expect(threads, isEmpty);
    });
  });

  group('InboxRepository.fetchMessages', () {
    test('calls GET /api/inbox/threads/{id}/messages and parses result',
        () async {
      final transport = _FakeTransport({
        'messages': [
          {'sender': 'Alice', 'text': 'hello', 'timestamp': '10:00', 'is_mine': false},
          {'sender': 'me', 'text': 'hey', 'timestamp': '10:01', 'is_mine': true},
        ],
        'thread': {'id': 't1', 'channel': 'whatsapp'},
      });
      final repo = InboxRepository(transport);
      final msgs = await repo.fetchMessages('t1');
      expect(transport.lastMethod, 'GET');
      expect(transport.lastPath, '/api/inbox/threads/t1/messages');
      expect(msgs.length, 2);
      expect(msgs.first.sender, 'Alice');
      expect(msgs.last.isMine, true);
    });

    test('handles empty messages list', () async {
      final transport = _FakeTransport({'messages': [], 'thread': {}});
      final repo = InboxRepository(transport);
      final msgs = await repo.fetchMessages('t99');
      expect(msgs, isEmpty);
    });
  });

  group('InboxRepository.markRead', () {
    test('calls POST /api/inbox/threads/{id}/read with empty body', () async {
      final transport = _FakeTransport({'ok': true});
      final repo = InboxRepository(transport);
      await repo.markRead('t1');
      expect(transport.lastMethod, 'POST');
      expect(transport.lastPath, '/api/inbox/threads/t1/read');
      expect(transport.lastBody, {});
    });
  });

  group('InboxRepository.reply', () {
    test('calls POST /api/inbox/threads/{id}/reply with text and mode',
        () async {
      final transport = _FakeTransport({'success': true, 'mode': 'direct'});
      final repo = InboxRepository(transport);
      final result = await repo.reply('t1', 'Hello there');
      expect(transport.lastMethod, 'POST');
      expect(transport.lastPath, '/api/inbox/threads/t1/reply');
      expect(transport.lastBody, {'text': 'Hello there', 'mode': 'direct'});
      expect(result['success'], true);
      expect(result['mode'], 'direct');
    });

    test('respects custom mode param', () async {
      final transport = _FakeTransport({'success': true, 'mode': 'suggest'});
      final repo = InboxRepository(transport);
      await repo.reply('t1', 'Draft text', mode: 'suggest');
      expect(transport.lastBody!['mode'], 'suggest');
    });
  });

  group('InboxRepository.fetchMedia', () {
    test('calls GET /api/inbox/threads/{id}/media/{mid} and returns bytes',
        () async {
      final transport = _FakeTransport({})
        ..bytesResponse = [0x4f, 0x67, 0x67, 0x53];
      final repo = InboxRepository(transport);
      final bytes = await repo.fetchMedia('t1', 'MSG1');
      expect(transport.lastMethod, 'GET-BYTES');
      expect(transport.lastPath, '/api/inbox/threads/t1/media/MSG1');
      expect(bytes, [0x4f, 0x67, 0x67, 0x53]);
    });

    test('propagates ApiError on transport failure', () {
      final transport = _ErrorTransport(ApiError(502, 'media download failed'));
      final repo = InboxRepository(transport);
      expect(() => repo.fetchMedia('t1', 'MSG1'), throwsA(isA<ApiError>()));
    });
  });

  group('InboxRepository channel instructions', () {
    test('getChannelInstruction maps GET and returns instruction', () async {
      final transport = _FakeTransport({
        'channel': 'whatsapp',
        'instruction': 'flag urgent messages',
      });
      final repo = InboxRepository(transport);
      final instruction = await repo.getChannelInstruction('whatsapp');
      expect(transport.lastMethod, 'GET');
      expect(transport.lastPath, '/api/inbox/channels/whatsapp/instruction');
      expect(instruction, 'flag urgent messages');
    });

    test('getChannelInstruction returns null when unset', () async {
      final transport =
          _FakeTransport({'channel': 'whatsapp', 'instruction': null});
      final repo = InboxRepository(transport);
      expect(await repo.getChannelInstruction('whatsapp'), isNull);
    });

    test('setChannelInstruction PUTs the instruction', () async {
      final transport = _FakeTransport({'success': true, 'created': true});
      final repo = InboxRepository(transport);
      final result =
          await repo.setChannelInstruction('email', 'auto-ack invoices');
      expect(transport.lastMethod, 'PUT');
      expect(transport.lastPath, '/api/inbox/channels/email/instruction');
      expect(transport.lastBody, {'instruction': 'auto-ack invoices'});
      expect(result['success'], true);
    });

    test('clearChannelInstruction DELETEs', () async {
      final transport = _FakeTransport({'success': true});
      final repo = InboxRepository(transport);
      await repo.clearChannelInstruction('whatsapp');
      expect(transport.lastMethod, 'DELETE');
      expect(transport.lastPath, '/api/inbox/channels/whatsapp/instruction');
    });
  });

  // ── Error propagation tests ────────────────────────────────────────────────

  group('InboxRepository error propagation', () {
    test('fetchThreads propagates ApiError on transport failure', () {
      final transport = _ErrorTransport(ApiError(500, 'server error'));
      final repo = InboxRepository(transport);
      expect(() => repo.fetchThreads(), throwsA(isA<ApiError>()));
    });

    test('reply propagates ApiError on transport failure', () {
      final transport = _ErrorTransport(ApiError(503, 'unavailable'));
      final repo = InboxRepository(transport);
      expect(() => repo.reply('t1', 'hi'), throwsA(isA<ApiError>()));
    });

    test('ApiError carries the status code and message', () async {
      final transport = _ErrorTransport(ApiError(404, 'thread not found'));
      final repo = InboxRepository(transport);
      try {
        await repo.fetchMessages('missing-id');
        fail('expected ApiError');
      } on ApiError catch (e) {
        expect(e.status, 404);
        expect(e.message, 'thread not found');
      }
    });
  });
}
