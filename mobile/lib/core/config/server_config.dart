import 'dart:async';
import 'dart:io';

import '../constants/app_constants.dart';

/// Probes a gateway for reachability. Returns `true` when the host answered the
/// health check with a 2xx, `false` on timeout / network error / non-2xx.
///
/// Injectable so [ServerConfig.resolveBaseUrl] can be unit-tested without real
/// network I/O.
typedef HealthProbe = Future<bool> Function(String baseUrl);

class ServerConfig {
  /// Short timeout for the startup reachability probe. Startup is never blocked
  /// longer than this (plus the connect handshake) before we fall through to the
  /// LAN host.
  static const Duration probeTimeout = Duration(seconds: 3);

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
  // mobile data alike — no URL flipping, no per-host re-login. load()/save()
  // return that single URL.
  static Future<String> load() async => kDefaultBaseUrl;

  static Future<void> save(String baseUrl) async {
    // no-op: server URL is locked in this build
  }

  /// GETs `/api/health` on [baseUrl] with a short timeout. Reachable = a 2xx
  /// status. Any failure (timeout, DNS/socket error, non-2xx) returns `false`.
  /// NEVER throws — exceptions are swallowed into `false` so the resolver's
  /// own try/catch only ever fires on truly unexpected failures.
  static Future<bool> _defaultProbe(String baseUrl) async {
    final client = HttpClient()..connectionTimeout = probeTimeout;
    try {
      final uri = Uri.parse('${normalizeBaseUrl(baseUrl)}/api/health');
      final request = await client.getUrl(uri).timeout(probeTimeout);
      // ngrok's free tier shows a browser interstitial unless this header is
      // set; harmless on the LAN/gateway path.
      request.headers.set('ngrok-skip-browser-warning', 'true');
      final response = await request.close().timeout(probeTimeout);
      // Drain so the socket can be reused/closed cleanly; ignore the body.
      await response.drain<void>().timeout(probeTimeout, onTimeout: () {});
      return response.statusCode >= 200 && response.statusCode < 300;
    } catch (_) {
      return false;
    } finally {
      client.close(force: true);
    }
  }

  /// Resolves the gateway used at startup.
  ///
  /// PREFERS [kDefaultBaseUrl] (the baked ngrok tunnel) and only falls back to
  /// [kLanFallbackBaseUrl] (the home-LAN mDNS host) when the tunnel is
  /// unreachable — e.g. the Mac is asleep or the ngrok session rotated. The
  /// in-app server picker was removed, so this self-heal is the app's only path
  /// to the server when the tunnel is down.
  ///
  /// Fail-safe contract: any UNEXPECTED error returns [kDefaultBaseUrl], which
  /// is the prior (pre-probe) behavior — so new code can never make startup
  /// worse than the locked-tunnel default. The probe itself never throws (it
  /// maps every failure to `false`); the outer guard exists only for the
  /// genuinely unexpected (e.g. a custom [probe] that throws).
  static Future<String> resolveBaseUrl({HealthProbe? probe}) async {
    final check = probe ?? _defaultProbe;
    try {
      final reachable = await check(kDefaultBaseUrl);
      return reachable ? kDefaultBaseUrl : kLanFallbackBaseUrl;
    } catch (_) {
      // Fail-safe: behave exactly as the locked build did before the probe.
      return kDefaultBaseUrl;
    }
  }
}
