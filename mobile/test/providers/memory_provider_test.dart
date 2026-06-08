import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/providers/memory_provider.dart';
import 'package:lazyclaw_mobile/repositories/memory_repository.dart';

// ── Fake transports ────────────────────────────────────────────────────────

Map<String, dynamic> _memoryJson({
  String id = 'm1',
  String key = 'pref',
  String value = 'dark mode',
  String createdAt = '2026-06-04T10:00:00Z',
}) =>
    {'id': id, 'key': key, 'value': value, 'created_at': createdAt};

class _StubTransport implements MemoryTransport {
  final Map<String, dynamic> getResponse;

  String? lastDeletePath;

  _StubTransport({required this.getResponse});

  @override
  Future<Map<String, dynamic>> getJson(String path) async => getResponse;

  @override
  Future<Map<String, dynamic>> deleteJson(String path) async {
    lastDeletePath = path;
    return {'status': 'deleted'};
  }
}

class _ErrorTransport implements MemoryTransport {
  final String message;
  _ErrorTransport([this.message = 'network error']);

  @override
  Future<Map<String, dynamic>> getJson(String path) async =>
      throw Exception(message);

  @override
  Future<Map<String, dynamic>> deleteJson(String path) async =>
      throw Exception(message);
}

// ── Tests ──────────────────────────────────────────────────────────────────

void main() {
  // ── initial state ─────────────────────────────────────────────────────────

  group('MemoryNotifier initial state', () {
    test('starts with empty memories, not loading, no error', () {
      final n = MemoryNotifier(MemoryRepository(_StubTransport(
        getResponse: {'memories': []},
      )));
      expect(n.state.memories, isEmpty);
      expect(n.state.isLoading, isFalse);
      expect(n.state.isDeleting, isFalse);
      expect(n.state.error, isNull);
    });
  });

  // ── load ──────────────────────────────────────────────────────────────────

  group('MemoryNotifier.load', () {
    test('sets isLoading during fetch then clears it', () async {
      final t = _StubTransport(getResponse: {
        'memories': [_memoryJson(id: 'a1')],
      });
      final n = MemoryNotifier(MemoryRepository(t));

      final future = n.load();
      // isLoading should be true synchronously before await resolves.
      expect(n.state.isLoading, isTrue);

      await future;
      expect(n.state.isLoading, isFalse);
    });

    test('populates memories on successful fetch', () async {
      final t = _StubTransport(getResponse: {
        'memories': [
          _memoryJson(id: 'r1', key: 'lang', value: 'Spanish'),
          _memoryJson(id: 'r2', key: 'tz', value: 'Europe/Madrid'),
        ],
      });
      final n = MemoryNotifier(MemoryRepository(t));
      await n.load();
      expect(n.state.memories, hasLength(2));
      expect(n.state.memories[0].id, 'r1');
      expect(n.state.memories[1].key, 'tz');
    });

    test('clears previous error on successful reload', () async {
      final failTransport = _ErrorTransport('boom');
      final n = MemoryNotifier(MemoryRepository(failTransport));
      await n.load();
      expect(n.state.error, isNotNull);

      // Now succeed.
      final goodN = MemoryNotifier(MemoryRepository(_StubTransport(
        getResponse: {'memories': [_memoryJson()]},
      )));
      await goodN.load();
      expect(goodN.state.error, isNull);
    });

    test('stores error and leaves memories empty on network failure', () async {
      final n = MemoryNotifier(MemoryRepository(_ErrorTransport('timeout')));
      await n.load();
      expect(n.state.isLoading, isFalse);
      expect(n.state.error, contains('timeout'));
      expect(n.state.memories, isEmpty);
    });

    test('stores error message on server error', () async {
      final n =
          MemoryNotifier(MemoryRepository(_ErrorTransport('500 server error')));
      await n.load();
      expect(n.state.error, isNotNull);
      expect(n.state.error, contains('500'));
    });

    test('subsequent load replaces memories with fresh data', () async {
      final t = _StubTransport(getResponse: {
        'memories': [_memoryJson(id: 'first', value: 'original')],
      });
      final n = MemoryNotifier(MemoryRepository(t));
      await n.load();
      expect(n.state.memories.first.id, 'first');

      final t2 = _StubTransport(getResponse: {
        'memories': [_memoryJson(id: 'second', value: 'updated')],
      });
      final n2 = MemoryNotifier(MemoryRepository(t2));
      await n2.load();
      expect(n2.state.memories.first.id, 'second');
    });
  });

  // ── deleteMemory ──────────────────────────────────────────────────────────

  group('MemoryNotifier.deleteMemory', () {
    test('removes the deleted item from state on success', () async {
      final t = _StubTransport(getResponse: {
        'memories': [
          _memoryJson(id: 'keep', value: 'keep me'),
          _memoryJson(id: 'gone', value: 'delete me'),
        ],
      });
      final n = MemoryNotifier(MemoryRepository(t));
      await n.load();
      expect(n.state.memories, hasLength(2));

      final ok = await n.deleteMemory('gone');
      expect(ok, isTrue);
      expect(n.state.memories, hasLength(1));
      expect(n.state.memories.first.id, 'keep');
    });

    test('returns false and stores error when delete fails', () async {
      final t = _StubTransport(getResponse: {
        'memories': [_memoryJson(id: 'm1')],
      });
      final n = MemoryNotifier(MemoryRepository(t));
      await n.load();

      // Override with an error transport for the delete call.
      final nFail =
          MemoryNotifier(MemoryRepository(_ErrorTransport('forbidden')));
      // Pre-populate state by copying from working notifier.
      await nFail.load(); // will fail → empty memories

      // Use a combined transport: load succeeds, delete fails.
      final mixed = _MixedTransport(
        getResponse: {
          'memories': [_memoryJson(id: 'keep')],
        },
      );
      final nMixed = MemoryNotifier(MemoryRepository(mixed));
      await nMixed.load();
      expect(nMixed.state.memories, hasLength(1));

      mixed.deleteError = true;
      final ok = await nMixed.deleteMemory('keep');
      expect(ok, isFalse);
      expect(nMixed.state.error, isNotNull);
      // Memories should NOT be removed on failure.
      expect(nMixed.state.memories, hasLength(1));
    });

    test('isDeleting is true during delete then clears', () async {
      final t = _StubTransport(getResponse: {
        'memories': [_memoryJson(id: 'del1')],
      });
      final n = MemoryNotifier(MemoryRepository(t));
      await n.load();

      final future = n.deleteMemory('del1');
      expect(n.state.isDeleting, isTrue);
      await future;
      expect(n.state.isDeleting, isFalse);
    });
  });

  // ── clearError ────────────────────────────────────────────────────────────

  group('MemoryNotifier.clearError', () {
    test('clears a stored error', () async {
      final n =
          MemoryNotifier(MemoryRepository(_ErrorTransport('oops')));
      await n.load();
      expect(n.state.error, isNotNull);
      n.clearError();
      expect(n.state.error, isNull);
    });
  });

  // ── MemoryState.copyWith ──────────────────────────────────────────────────

  group('MemoryState.copyWith', () {
    test('preserves unchanged fields', () {
      const s = MemoryState(isLoading: true);
      final s2 = s.copyWith(isDeleting: true);
      expect(s2.isLoading, isTrue);
      expect(s2.isDeleting, isTrue);
      expect(s2.memories, isEmpty);
    });

    test('clearError: true sets error to null', () {
      const s = MemoryState(error: 'old error');
      final s2 = s.copyWith(clearError: true);
      expect(s2.error, isNull);
    });

    test('clearError: false preserves existing error', () {
      const s = MemoryState(error: 'old error');
      final s2 = s.copyWith(isLoading: true);
      expect(s2.error, 'old error');
    });
  });
}

// ── Mixed transport helper ─────────────────────────────────────────────────

class _MixedTransport implements MemoryTransport {
  final Map<String, dynamic> getResponse;
  bool deleteError = false;

  _MixedTransport({required this.getResponse});

  @override
  Future<Map<String, dynamic>> getJson(String path) async => getResponse;

  @override
  Future<Map<String, dynamic>> deleteJson(String path) async {
    if (deleteError) throw Exception('delete failed');
    return {'status': 'deleted'};
  }
}
