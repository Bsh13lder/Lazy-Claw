// Covers [ServerAliasRegistry] + its integration with the offline auth cache:
//   * the static [kServerAliases] are always aliases;
//   * a DISCOVERED host (LAN sweep result) registered at runtime becomes an
//     alias, so switching to it does NOT invalidate the cached identity — the
//     user is not force-logged-out when DHCP moves the Mac;
//   * a foreign URL is never an alias, so a cached identity still cannot
//     unlock offline mode against a different server.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/auth/auth_cache.dart';
import 'package:lazyclaw_mobile/core/config/server_aliases.dart';
import 'package:lazyclaw_mobile/core/constants/app_constants.dart';
import 'package:lazyclaw_mobile/models/user.dart';

User _user() => User.fromJson(const {
      'id': 'u1',
      'username': 'blckit',
    });

void main() {
  setUp(ServerAliasRegistry.resetForTests);
  tearDown(ServerAliasRegistry.resetForTests);

  group('ServerAliasRegistry', () {
    test('every static alias is recognized', () {
      for (final url in kServerAliases) {
        expect(ServerAliasRegistry.isAlias(url), isTrue, reason: url);
      }
    });

    test('an unknown URL is not an alias', () {
      expect(ServerAliasRegistry.isAlias('http://evil.example:18789'), isFalse);
    });

    test('add() registers a discovered host as an alias (normalized)', () {
      ServerAliasRegistry.add('192.168.0.23:18789');
      expect(
          ServerAliasRegistry.isAlias('http://192.168.0.23:18789'), isTrue);
    });

    test('seed() registers many at once', () {
      ServerAliasRegistry.seed(
          ['http://192.168.0.23:18789', 'http://192.168.230.101:18789']);
      expect(ServerAliasRegistry.isAlias('http://192.168.0.23:18789'), isTrue);
      expect(
          ServerAliasRegistry.isAlias('http://192.168.230.101:18789'), isTrue);
    });
  });

  group('auth cache with dynamic aliases', () {
    test(
        'identity cached under the DuckDNS host unlocks under a DISCOVERED '
        'LAN host (no forced re-login after a DHCP move)', () {
      ServerAliasRegistry.add('http://192.168.0.23:18789');
      final raw = encodeAuthCachePayload(_user(), kDefaultBaseUrl);
      final u =
          decodeAuthCachePayload(raw, baseUrl: 'http://192.168.0.23:18789');
      expect(u, isNotNull);
      expect(u!.id, 'u1');
    });

    test('identity cached under a discovered host unlocks under a static alias',
        () {
      ServerAliasRegistry.add('http://192.168.0.23:18789');
      final raw =
          encodeAuthCachePayload(_user(), 'http://192.168.0.23:18789');
      final u = decodeAuthCachePayload(raw, baseUrl: kDefaultBaseUrl);
      expect(u, isNotNull);
    });

    test('a foreign server URL still never unlocks the cached identity', () {
      final raw = encodeAuthCachePayload(_user(), kDefaultBaseUrl);
      final u = decodeAuthCachePayload(raw,
          baseUrl: 'http://someone-elses-box.example:18789');
      expect(u, isNull);
    });

    test('exact-match still works with an empty registry', () {
      final raw = encodeAuthCachePayload(_user(), 'http://box.lan:9000');
      expect(
          decodeAuthCachePayload(raw, baseUrl: 'http://box.lan:9000'),
          isNotNull);
    });
  });
}
