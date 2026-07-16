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

  /// The production store for the LAST-KNOWN-GOOD auto-resolved host. Kept as a
  /// named constant so tests can restore it after swapping in an in-memory one.
  static const BaseUrlOverrideStore defaultLastResolvedStore =
      SecureBaseUrlOverrideStore(storageKey: 'settings.last_resolved_base_url');

  /// Persists the host [resolveBaseUrl] last settled on (via [rememberResolved])
  /// so [seedBaseUrl] can seed the NEXT cold start directly to it — no probe,
  /// and no background URL switch on the common repeat-launch path. Swappable
  /// for an in-memory store in tests.
  static BaseUrlOverrideStore lastResolvedStore = defaultLastResolvedStore;

  /// The last auto-resolved reachable host, or `null` when none recorded yet.
  /// Never throws — a failing store resolves to `null`.
  static Future<String?> loadLastResolved() async {
    try {
      return await lastResolvedStore.load();
    } catch (_) {
      return null;
    }
  }

  /// Record [url] as the last-known-good host (called after [resolveBaseUrl]
  /// settles, from [GatewayController.reresolve]). Best-effort — never throws.
  static Future<void> rememberResolved(String url) async {
    try {
      await lastResolvedStore.save(normalizeBaseUrl(url));
    } catch (_) {
      // Persistence is an optimization for the NEXT launch; a failure here must
      // never disrupt the current one.
    }
  }

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
  /// 2. [kLanFallbackIpBaseUrl] — the Mac's LAN IP directly. Probed BEFORE the
  ///    mDNS name because Dart's `HttpClient` frequently can't resolve `.local`
  ///    (it hangs the full probe timeout, then fails), so the raw IP is the
  ///    reliable home-WiFi host and must be tried first — otherwise resolution
  ///    wastes a timeout on `.local` (or mis-lands on a flaky `.local` probe)
  ///    before reaching the IP that actually answers.
  /// 3. [kLanFallbackBaseUrl] — the Mac's mDNS name. Kept LAST as a best-effort
  ///    extra for platforms whose HTTP client *can* resolve `.local`.
  static const List<String> _candidates = [
    kDefaultBaseUrl,
    kLanFallbackIpBaseUrl,
    kLanFallbackBaseUrl,
  ];

  /// The NON-BLOCKING startup seed for the active gateway.
  ///
  /// Order (all cheap local reads — NO network probe):
  ///   1. the saved manual OVERRIDE (the user's explicit pin wins);
  ///   2. else the LAST-KNOWN-GOOD auto-resolved host (so a home-WiFi repeat
  ///      launch seeds straight to the LAN host that answered last time, and the
  ///      background [GatewayController.reresolve] then resolves to that SAME
  ///      host — no URL switch, so `authProvider` is not rebuilt out from under
  ///      a just-restored session);
  ///   3. else [kDefaultBaseUrl].
  ///
  /// This is what `main()` awaits before `runApp()`, so the first frame renders
  /// instantly instead of blocking 3s-per-host on [resolveBaseUrl]'s
  /// reachability probes — a cold widget launch is ALWAYS a full `main()`, so
  /// that probe was a multi-second black screen on every such launch.
  /// Reachability-based host selection is deferred to a post-first-frame
  /// background [GatewayController.reresolve]. Never throws (a failing store
  /// yields the default so seeding always makes progress).
  static Future<String> seedBaseUrl() async =>
      (await loadOverride()) ?? (await loadLastResolved()) ?? kDefaultBaseUrl;

  /// Resolves the gateway to use.
  ///
  /// 1. If a manual OVERRIDE is set, it is tried FIRST. When reachable it's
  ///    returned — the user's explicit choice wins.
  /// 2. If the override is set but UNREACHABLE, we do NOT blindly return it:
  ///    we fall through to the [_candidates] and return the first that answers.
  ///    A dead override (e.g. pinned to a since-vanished LAN IP) must never
  ///    brick the app — the login screen has no server-URL field to escape it,
  ///    so an unreachable pin used to be a hard lockout. The explicit choice is
  ///    only set aside when a DIFFERENT host actually answers.
  /// 3. Otherwise the [_candidates] are probed in order and the FIRST that
  ///    answers `/api/health` is returned, so the app self-heals whether it's on
  ///    cellular (public front door) or home WiFi (LAN host).
  /// 4. If nothing answers at all: with an override set we return it (the
  ///    explicit choice is preserved as a last resort, diagnosable via
  ///    [probeAll]); otherwise we fall back to [kDefaultBaseUrl] so behavior
  ///    matches the pre-probe build.
  static Future<String> resolveBaseUrl({HealthProbe? probe}) async {
    final check = probe ?? _defaultProbe;

    final override = await loadOverride();
    if (override != null) {
      try {
        if (await check(override)) return override;
      } catch (_) {
        // Probe threw — treat as unreachable and fall through to candidates.
      }
      // Override unreachable: prefer any auto candidate that actually answers
      // over a dead pin (anti-brick). Skip re-probing the override itself.
      for (final url in _candidates) {
        if (url == override) continue;
        try {
          if (await check(url)) return url;
        } catch (_) {
          // Probe threw unexpectedly — treat as unreachable, try the next.
        }
      }
      // Nothing else reachable either — keep the user's explicit choice.
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
