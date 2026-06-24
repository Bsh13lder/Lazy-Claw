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
    test('primary reachable → returns primary tunnel URL', () async {
      String? probed;
      final url = await ServerConfig.resolveBaseUrl(probe: (b) async {
        probed = b;
        return true;
      });
      expect(url, kDefaultBaseUrl);
      // The probe must target the primary tunnel, not the LAN host.
      expect(probed, kDefaultBaseUrl);
    });

    test('primary unreachable → falls back to LAN host', () async {
      final url = await ServerConfig.resolveBaseUrl(probe: (b) async => false);
      expect(url, kLanFallbackBaseUrl);
    });

    test('probe throws → fail-safe to primary (current behavior)', () async {
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
