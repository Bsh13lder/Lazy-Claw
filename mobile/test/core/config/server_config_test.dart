import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/config/server_config.dart';
import 'package:lazyclaw_mobile/core/constants/app_constants.dart';

void main() {
  test('normalizeBaseUrl strips trailing slash and adds scheme', () {
    expect(ServerConfig.normalizeBaseUrl('192.168.1.5:18789'),
        'http://192.168.1.5:18789');
    expect(ServerConfig.normalizeBaseUrl('https://box.local/'),
        'https://box.local');
    expect(ServerConfig.normalizeBaseUrl('http://x:18789'), 'http://x:18789');
  });

  test('wsUrlFor converts http(s) base to ws(s) /ws/chat', () {
    expect(ServerConfig.wsUrlFor('http://x:18789'), 'ws://x:18789/ws/chat');
    expect(ServerConfig.wsUrlFor('https://box.local'), 'wss://box.local/ws/chat');
  });

  group('resolveBaseUrl', () {
    test('primary reachable → returns primary', () async {
      final probed = <String>[];
      final url = await ServerConfig.resolveBaseUrl(probe: (b) async {
        probed.add(b);
        return true;
      });
      expect(url, kDefaultBaseUrl);
      // Candidates are probed as a PARALLEL batch (so the worst case is one
      // probe timeout, not one per candidate) and the winner is picked by
      // priority — the primary outranks every LAN/loopback candidate.
      expect(probed, contains(kDefaultBaseUrl));
    });

    test('primary unreachable, mDNS reachable → returns mDNS LAN host',
        () async {
      final url = await ServerConfig.resolveBaseUrl(
        probe: (b) async => b == kLanFallbackBaseUrl,
      );
      expect(url, kLanFallbackBaseUrl);
    });

    test('only the LAN IP answers → returns the LAN IP host', () async {
      final url = await ServerConfig.resolveBaseUrl(
        probe: (b) async => b == kLanFallbackIpBaseUrl,
      );
      expect(url, kLanFallbackIpBaseUrl);
    });

    test('nothing reachable → fail-safe to primary', () async {
      final url = await ServerConfig.resolveBaseUrl(probe: (b) async => false);
      expect(url, kDefaultBaseUrl);
    });

    test('probe throws for every candidate → fail-safe to primary', () async {
      final url = await ServerConfig.resolveBaseUrl(
        probe: (b) async => throw StateError('boom'),
      );
      expect(url, kDefaultBaseUrl);
    });

    test('probe throws synchronously → fail-safe to primary', () async {
      final url = await ServerConfig.resolveBaseUrl(
        // ignore: only_throw_errors
        probe: (b) => throw 'sync boom',
      );
      expect(url, kDefaultBaseUrl);
    });
  });
}
