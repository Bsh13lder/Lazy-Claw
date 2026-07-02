import 'dart:async';
import 'dart:io';

import '../constants/app_constants.dart';
import 'base_url_override_store.dart';

/// Probes a gateway for reachability. Returns `true` when the host answered the
/// health check with a 2xx, `false` on timeout / network error / non-2xx.
///
/// Injectable so [ServerConfig.resolveBaseUrl] can be unit-tested without real
/// network I/O.
typedef HealthProbe = Future<bool> Function(String baseUrl);

/// One candidate gateway with its human label and last-probed reachability.
/// Immutable — produced by [ServerConfig.probeAll] for the diagnostics UI so
/// the user can SEE which hosts the phone can actually reach.
class GatewayStatus {
  final String url;
  final String label;
  final bool reachable;

  const GatewayStatus({
    required this.url,
    required this.label,
    required this.reachable,
  });
}

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

  // ── Manual override persistence ─────────────────────────────────────────

  /// Pluggable override store. Defaults to secure storage; tests swap in an
  /// [InMemoryBaseUrlOverrideStore]. A user-set URL takes precedence over the
  /// auto candidates (see [resolveBaseUrl]) so the app is never permanently
  /// pinned to a host it can't reach.
  static BaseUrlOverrideStore overrideStore = const SecureBaseUrlOverrideStore();

  /// The user's saved override, or `null` when unset. Never throws — a failing
  /// store resolves to `null` so resolution always makes progress.
  static Future<String?> loadOverride() async {
    try {
      final v = await overrideStore.load();
      if (v == null) return null;
      final t = v.trim();
      return t.isEmpty ? null : t;
    } catch (_) {
      return null;
    }
  }

  /// Persist [url] as the manual override, normalizing it first.
  static Future<void> saveOverride(String url) =>
      overrideStore.save(normalizeBaseUrl(url));

  /// Remove the manual override (revert to automatic resolution).
  static Future<void> clearOverride() => overrideStore.clear();

  /// Back-compat: the effective saved URL (override if set, else the primary).
  static Future<String> load() async =>
      (await loadOverride()) ?? kDefaultBaseUrl;

  /// Back-compat alias for [saveOverride].
  static Future<void> save(String baseUrl) => saveOverride(baseUrl);

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

  /// Resolves the gateway to use.
  ///
  /// 1. If a manual OVERRIDE is set, it is tried FIRST. When reachable it's
  ///    returned; when set-but-unreachable it is STILL returned — the user's
  ///    explicit choice is never silently discarded (they can diagnose it via
  ///    [probeAll]).
  /// 2. Otherwise the [_candidates] are probed in order and the FIRST that
  ///    answers `/api/health` is returned, so the app self-heals whether it's on
  ///    cellular (public front door) or home WiFi (LAN host).
  /// 3. If nothing answers — server asleep / no network — it falls back to
  ///    [kDefaultBaseUrl] so behavior matches the pre-probe build.
  static Future<String> resolveBaseUrl({HealthProbe? probe}) async {
    final check = probe ?? _defaultProbe;

    final override = await loadOverride();
    if (override != null) {
      try {
        if (await check(override)) return override;
      } catch (_) {
        // Probe threw — fall through, but still prefer the explicit choice.
      }
      return override;
    }

    for (final url in _candidates) {
      try {
        if (await check(url)) return url;
      } catch (_) {
        // Probe threw unexpectedly — treat as unreachable, try the next.
      }
    }
    return kDefaultBaseUrl;
  }

  /// Probes EVERY known gateway (the override, if set, then the auto
  /// candidates) and returns each with its reachability. Used by the Settings
  /// "Test connection" diagnostics so a silent-failure ("phone can't reach
  /// 192.168.0.12") becomes visible. Duplicates (an override equal to a
  /// candidate) are collapsed to a single entry.
  static Future<List<GatewayStatus>> probeAll({HealthProbe? probe}) async {
    final check = probe ?? _defaultProbe;
    final override = await loadOverride();

    final planned = <({String url, String label})>[
      if (override != null) (url: override, label: 'Custom (saved)'),
      (url: kDefaultBaseUrl, label: 'Remote (DuckDNS)'),
      (url: kLanFallbackBaseUrl, label: 'LAN (mDNS)'),
      (url: kLanFallbackIpBaseUrl, label: 'LAN (IP)'),
    ];

    final seen = <String>{};
    final results = <GatewayStatus>[];
    for (final entry in planned) {
      final norm = normalizeBaseUrl(entry.url);
      if (!seen.add(norm)) continue; // dedup override-equals-candidate
      bool reachable;
      try {
        reachable = await check(entry.url);
      } catch (_) {
        reachable = false;
      }
      results.add(GatewayStatus(
        url: entry.url,
        label: entry.label,
        reachable: reachable,
      ));
    }
    return results;
  }
}
