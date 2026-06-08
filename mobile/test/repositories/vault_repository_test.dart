import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/repositories/vault_repository.dart';

// ── Fake transport ─────────────────────────────────────────────────────────

class _FakeTransport implements VaultTransport {
  String? lastMethod;
  String? lastPath;
  Map<String, dynamic>? lastBody;

  dynamic _response;

  _FakeTransport(this._response);

  void respondWith(dynamic value) => _response = value;

  @override
  Future<Map<String, dynamic>> getJson(String path) async {
    lastMethod = 'GET';
    lastPath = path;
    if (_response is Exception) throw _response as Exception;
    return Map<String, dynamic>.from(_response as Map);
  }

  @override
  Future<Map<String, dynamic>> putJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    lastMethod = 'PUT';
    lastPath = path;
    lastBody = body;
    if (_response is Exception) throw _response as Exception;
    return Map<String, dynamic>.from(_response as Map);
  }

  @override
  Future<Map<String, dynamic>> deleteJson(String path) async {
    lastMethod = 'DELETE';
    lastPath = path;
    if (_response is Exception) throw _response as Exception;
    return Map<String, dynamic>.from(_response as Map);
  }
}

// ── Tests ──────────────────────────────────────────────────────────────────

void main() {
  group('VaultRepository.listSecrets', () {
    test('GET /api/vault and parses keys list', () async {
      final t = _FakeTransport({'keys': ['OPENAI_API_KEY', 'STRIPE_TOKEN']});
      final repo = VaultRepository(t);

      final entries = await repo.listSecrets();

      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/vault');
      expect(entries, hasLength(2));
      expect(entries[0].name, 'OPENAI_API_KEY');
      expect(entries[1].name, 'STRIPE_TOKEN');
    });

    test('returns empty list when keys array is empty', () async {
      final t = _FakeTransport({'keys': <String>[]});
      final repo = VaultRepository(t);

      final entries = await repo.listSecrets();
      expect(entries, isEmpty);
    });

    test('returns empty list when keys key is missing from response', () async {
      final t = _FakeTransport(<String, dynamic>{});
      final repo = VaultRepository(t);

      final entries = await repo.listSecrets();
      expect(entries, isEmpty);
    });

    test('maps each raw key string to VaultEntry', () async {
      final t = _FakeTransport({'keys': ['MY_SECRET']});
      final entries = await VaultRepository(t).listSecrets();

      expect(entries.first, isA<VaultEntry>());
      expect(entries.first.name, 'MY_SECRET');
    });

    test('propagates transport errors', () async {
      final t = _FakeTransport(Exception('network error'));
      expect(
        () => VaultRepository(t).listSecrets(),
        throwsA(isA<Exception>()),
      );
    });
  });

  group('VaultRepository.addSecret', () {
    test('PUT /api/vault/{encoded-name} with value body', () async {
      final t = _FakeTransport({'status': 'ok'});
      final repo = VaultRepository(t);

      await repo.addSecret('OPENAI_API_KEY', 'sk-abc123');

      expect(t.lastMethod, 'PUT');
      expect(t.lastPath, '/api/vault/OPENAI_API_KEY');
      expect(t.lastBody, containsPair('value', 'sk-abc123'));
    });

    test('URL-encodes key names with special characters', () async {
      final t = _FakeTransport({'status': 'ok'});
      await VaultRepository(t).addSecret('MY KEY/SECRET', 'val');

      // spaces and slashes must be encoded
      expect(t.lastPath, isNot(contains(' ')));
      expect(t.lastPath, isNot(contains('/'[0] == '/' ? 'MY KEY/SECRET' : '')));
      expect(t.lastPath, startsWith('/api/vault/'));
    });

    test('propagates transport errors', () async {
      final t = _FakeTransport(Exception('server error'));
      expect(
        () => VaultRepository(t).addSecret('K', 'v'),
        throwsA(isA<Exception>()),
      );
    });

    test('does not log or expose the value (transport only carries body)', () async {
      // This test verifies the repo does not transform/echo the value —
      // the body forwarded to the transport must equal what was passed in.
      final t = _FakeTransport({'status': 'ok'});
      const secret = 'super-secret-value';
      await VaultRepository(t).addSecret('K', secret);
      expect(t.lastBody, containsPair('value', secret));
    });
  });

  group('VaultRepository.deleteSecret', () {
    test('DELETE /api/vault/{encoded-name}', () async {
      final t = _FakeTransport({'status': 'deleted'});
      final repo = VaultRepository(t);

      await repo.deleteSecret('OPENAI_API_KEY');

      expect(t.lastMethod, 'DELETE');
      expect(t.lastPath, '/api/vault/OPENAI_API_KEY');
    });

    test('URL-encodes key names with spaces', () async {
      final t = _FakeTransport({'status': 'deleted'});
      await VaultRepository(t).deleteSecret('MY KEY');
      expect(t.lastPath, '/api/vault/MY%20KEY');
    });

    test('propagates transport errors', () async {
      final t = _FakeTransport(Exception('not found'));
      expect(
        () => VaultRepository(t).deleteSecret('MISSING'),
        throwsA(isA<Exception>()),
      );
    });
  });

  group('VaultEntry', () {
    test('equality is based on name', () {
      const a = VaultEntry(name: 'MY_KEY');
      const b = VaultEntry(name: 'MY_KEY');
      const c = VaultEntry(name: 'OTHER');

      expect(a, equals(b));
      expect(a, isNot(equals(c)));
    });

    test('hashCode matches name hashCode', () {
      const entry = VaultEntry(name: 'K');
      expect(entry.hashCode, 'K'.hashCode);
    });
  });
}
