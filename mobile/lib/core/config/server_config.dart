import '../constants/app_constants.dart';

class ServerConfig {
  static String normalizeBaseUrl(String raw) {
    var v = raw.trim();
    if (!v.startsWith('http://') && !v.startsWith('https://')) {
      v = 'http://$v';
    }
    if (v.endsWith('/')) v = v.substring(0, v.length - 1);
    return v;
  }

  static String wsUrlFor(String baseUrl) {
    final b = normalizeBaseUrl(baseUrl);
    final ws = b.startsWith('https://')
        ? 'wss://${b.substring('https://'.length)}'
        : 'ws://${b.substring('http://'.length)}';
    return '$ws/ws/chat';
  }

  // Locked build: the server URL is baked into [kDefaultBaseUrl] (the ngrok
  // tunnel) and cannot be changed from inside the app. The app talks to exactly
  // ONE host so the session cookie + auth cache stay consistent on WiFi and
  // mobile data alike — no URL flipping, no per-host re-login. load()/save() and
  // the startup resolver all return that single URL.
  static Future<String> load() async => kDefaultBaseUrl;

  static Future<void> save(String baseUrl) async {
    // no-op: server URL is locked in this build
  }

  /// Single, fixed gateway used at startup. Always the baked tunnel URL — the
  /// Mac runs the ngrok service (launchd, auto-restart), so this is reachable on
  /// WiFi and cellular alike.
  static Future<String> resolveBaseUrl() async => kDefaultBaseUrl;
}
