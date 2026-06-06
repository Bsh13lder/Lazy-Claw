import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/repositories/memory_repository.dart';

// ── Fake transport ─────────────────────────────────────────────────────────

class _FakeTransport implements MemoryTransport {
  String? lastPath;
  String? lastMethod;

  /// Response to return on the next call.
  Map<String, dynamic> response;

  _FakeTransport(this.response);

  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    lastMethod = 'GET';
    lastPath = path;
    return response;
  }

  @override
  Future<Map<String, dynamic>> deleteJson(String path) async {
    lastMethod = 'DELETE';
    lastPath = path;
    return response;
  }
}

// ── Fixtures ───────────────────────────────────────────────────────────────

Map<String, dynamic> _memoryJson({
  String id = 'm1',
  String key = 'user_preference',
  String value = 'Prefers dark mode',
  String createdAt = '2026-06-04T10:00:00Z',
}) =>
    {
      'id': id,
      'key': key,
      'value': value,
      'created_at': createdAt,
    };

// ── Tests ──────────────────────────────────────────────────────────────────

void main() {
  // ── listMemories ─────────────────────────────────────────────────────────

  group('MemoryRepository.listMemories', () {
    test('issues GET /api/memory/personal', () async {
      final t = _FakeTransport({'memories': []});
      await MemoryRepository(t).listMemories();
      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/memory/personal');
    });

    test('parses returned memories list', () async {
      final t = _FakeTransport({
        'memories': [
          _memoryJson(id: 'a1', key: 'lang', value: 'Spanish'),
          _memoryJson(id: 'a2', key: 'theme', value: 'dark'),
        ],
      });
      final memories = await MemoryRepository(t).listMemories();
      expect(memories, hasLength(2));
      expect(memories[0].id, 'a1');
      expect(memories[0].key, 'lang');
      expect(memories[0].value, 'Spanish');
      expect(memories[1].id, 'a2');
    });

    test('maps all PersonalMemory fields correctly', () async {
      final t = _FakeTransport({
        'memories': [
          _memoryJson(
            id: 'x1',
            key: 'timezone',
            value: 'Europe/Madrid',
            createdAt: '2026-01-15T08:30:00Z',
          ),
        ],
      });
      final memories = await MemoryRepository(t).listMemories();
      final m = memories.first;
      expect(m.id, 'x1');
      expect(m.key, 'timezone');
      expect(m.value, 'Europe/Madrid');
      expect(m.createdAt, '2026-01-15T08:30:00Z');
    });

    test('returns empty list when memories key is missing', () async {
      final t = _FakeTransport({});
      final memories = await MemoryRepository(t).listMemories();
      expect(memories, isEmpty);
    });

    test('returns empty list when memories array is empty', () async {
      final t = _FakeTransport({'memories': []});
      final memories = await MemoryRepository(t).listMemories();
      expect(memories, isEmpty);
    });

    test('returns empty list when memories key is null', () async {
      final t = _FakeTransport({'memories': null});
      final memories = await MemoryRepository(t).listMemories();
      expect(memories, isEmpty);
    });

    test('tolerates null field values in a memory object (defensive parse)',
        () async {
      final t = _FakeTransport({
        'memories': [
          {'id': null, 'key': null, 'value': null, 'created_at': null},
        ],
      });
      final memories = await MemoryRepository(t).listMemories();
      expect(memories, hasLength(1));
      expect(memories.first.id, '');
      expect(memories.first.key, '');
      expect(memories.first.value, '');
      expect(memories.first.createdAt, '');
    });

    test('parses a large list without error', () async {
      final items = List.generate(
        50,
        (i) => _memoryJson(id: 'id$i', key: 'key$i', value: 'value $i'),
      );
      final t = _FakeTransport({'memories': items});
      final memories = await MemoryRepository(t).listMemories();
      expect(memories, hasLength(50));
      expect(memories.last.id, 'id49');
    });
  });

  // ── deleteMemory ──────────────────────────────────────────────────────────

  group('MemoryRepository.deleteMemory', () {
    test('issues DELETE /api/memory/personal/{id}', () async {
      final t = _FakeTransport({'status': 'deleted'});
      await MemoryRepository(t).deleteMemory('m42');
      expect(t.lastMethod, 'DELETE');
      expect(t.lastPath, '/api/memory/personal/m42');
    });

    test('uses the exact id in the path (no encoding changes for simple ids)',
        () async {
      final t = _FakeTransport({'status': 'ok'});
      await MemoryRepository(t).deleteMemory('abc-123');
      expect(t.lastPath, '/api/memory/personal/abc-123');
    });

    test('completes without throwing when server returns status: deleted',
        () async {
      final t = _FakeTransport({'status': 'deleted'});
      await expectLater(
        MemoryRepository(t).deleteMemory('some-id'),
        completes,
      );
    });

    test('propagates transport errors as exceptions', () async {
      final t = _ErrorTransport();
      await expectLater(
        MemoryRepository(t).deleteMemory('bad'),
        throwsA(isA<Exception>()),
      );
    });
  });

  // ── PersonalMemory model ──────────────────────────────────────────────────

  group('PersonalMemory.fromJson', () {
    test('parses complete JSON', () {
      final m = PersonalMemory.fromJson(_memoryJson(
        id: 'z1',
        key: 'pref',
        value: 'hello',
        createdAt: '2026-03-01T00:00:00Z',
      ));
      expect(m.id, 'z1');
      expect(m.key, 'pref');
      expect(m.value, 'hello');
      expect(m.createdAt, '2026-03-01T00:00:00Z');
    });

    test('converts non-string id to string', () {
      final m = PersonalMemory.fromJson({'id': 99, 'key': 'k', 'value': 'v', 'created_at': 't'});
      expect(m.id, '99');
    });

    test('returns empty strings for missing fields', () {
      final m = PersonalMemory.fromJson({});
      expect(m.id, '');
      expect(m.key, '');
      expect(m.value, '');
      expect(m.createdAt, '');
    });

    test('toString includes id and key', () {
      final m = PersonalMemory.fromJson(_memoryJson(id: 'zzz', key: 'mykey'));
      expect(m.toString(), contains('zzz'));
      expect(m.toString(), contains('mykey'));
    });
  });
}

// ── Error transport helper ─────────────────────────────────────────────────

class _ErrorTransport implements MemoryTransport {
  @override
  Future<Map<String, dynamic>> getJson(String path) async =>
      throw Exception('network error');

  @override
  Future<Map<String, dynamic>> deleteJson(String path) async =>
      throw Exception('network error');
}
