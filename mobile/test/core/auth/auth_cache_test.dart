// Tests the offline-auth cache codec + the in-memory cache used by the auth
// tests. The codec is shared by [SecureAuthUserCache] and
// [InMemoryAuthUserCache], so exercising it here covers the contract for both:
// read() returns null on missing payload / parse failure / base_url mismatch
// and NEVER throws.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/auth/auth_cache.dart';
import 'package:lazyclaw_mobile/models/user.dart';

const _user = User(id: 'u1', username: 'sam', displayName: 'Sam', role: 'user');
const _base = 'http://192.168.1.10:18789';

void main() {
  group('auth cache codec', () {
    test('round-trips a user for a matching base_url', () {
      final raw = encodeAuthCachePayload(_user, _base);
      final restored = decodeAuthCachePayload(raw, baseUrl: _base);
      expect(restored, isNotNull);
      expect(restored!.id, 'u1');
      expect(restored.username, 'sam');
      expect(restored.displayName, 'Sam');
      expect(restored.role, 'user');
    });

    test('returns null on base_url mismatch', () {
      final raw = encodeAuthCachePayload(_user, _base);
      expect(
        decodeAuthCachePayload(raw, baseUrl: 'http://other-host:18789'),
        isNull,
      );
    });

    test('returns null on missing payload', () {
      expect(decodeAuthCachePayload(null, baseUrl: _base), isNull);
      expect(decodeAuthCachePayload('', baseUrl: _base), isNull);
    });

    test('returns null (never throws) on garbage payload', () {
      expect(decodeAuthCachePayload('not json at all', baseUrl: _base), isNull);
      expect(decodeAuthCachePayload('[1,2,3]', baseUrl: _base), isNull);
      expect(
        decodeAuthCachePayload('{"base_url":"$_base","user":42}',
            baseUrl: _base),
        isNull,
      );
    });
  });

  group('InMemoryAuthUserCache', () {
    test('write/read/clear lifecycle', () async {
      final cache = InMemoryAuthUserCache();
      expect(await cache.read(baseUrl: _base), isNull);

      await cache.write(_user, baseUrl: _base);
      final hit = await cache.read(baseUrl: _base);
      expect(hit, isNotNull);
      expect(hit!.username, 'sam');

      await cache.clear();
      expect(await cache.read(baseUrl: _base), isNull);
    });

    test('read returns null when the stored base_url differs', () async {
      final cache = InMemoryAuthUserCache();
      await cache.write(_user, baseUrl: _base);
      expect(await cache.read(baseUrl: 'http://elsewhere:1'), isNull);
    });
  });
}
