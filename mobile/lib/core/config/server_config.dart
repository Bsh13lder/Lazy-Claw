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

  // Locked build: the server URL is baked into [kDefaultBaseUrl] (the
  // self-hosted DuckDNS + Caddy front door) and cannot be changed from inside
  // the app. The app talks to exactly
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

  /// The gateways the app will try at startup, in preference order.
  ///
  /// 1. [kDefaultBaseUrl] — the public DuckDNS + Caddy front door. Works on
  ///    cellular and away from home.
  /// 2. [kLanFallbackBaseUrl] — the Mac's mDNS name. Works on home WiFi, where
  ///    the router won't hairpin the public IP back inside, so the public URL
  ///    is unreachable there.
  /// 3. [kLanFallbackIpBaseUrl] — the Mac's LAN IP directly. Same job as (2)
  ///    for platforms whose HTTP client can't resolve `.local`.
  static const List<String> _candidates = [
    kDefaultBaseUrl,
    kLanFallbackBaseUrl,
    kLanFallbackIpBaseUrl,
  ];

  /// Resolves the gateway used at startup by probing [_candidates] in order and
  /// returning the FIRST that actually answers `/api/health`. Every candidate is
  /// verified (we never hand back an unreachable URL we merely assumed), so the
  /// app self-heals whether it's on cellular (public front door) or home WiFi
  /// (LAN host). If nothing answers — server asleep / no network — it falls back
  /// to [kDefaultBaseUrl] so behavior matches the pre-probe build.
  static Future<String> resolveBaseUrl({HealthProbe? probe}) async {
    final check = probe ?? _defaultProbe;
    for (final url in _candidates) {
      try {
        if (await check(url)) return url;
      } catch (_) {
        // Probe threw unexpectedly — treat as unreachable, try the next.
      }
    }
    return kDefaultBaseUrl;
  }
}
