import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/config/server_config.dart';

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
}
