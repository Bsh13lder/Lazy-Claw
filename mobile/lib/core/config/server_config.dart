import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../constants/app_constants.dart';
import 'base_url_override_store.dart';
import 'lan_discovery.dart';
import 'server_aliases.dart';

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

/// The outcome of [ServerConfig.resolveGateway]: the URL to use plus whether a
/// host actually ANSWERED. `reachable: false` means [url] is only the
/// fail-safe (override or default) — callers must not persist it as the
/// last-known-good host, or an unreachable fail-safe clobbers a good seed.
typedef ResolvedGateway = ({String url, bool reachable});

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

  // ── Discovered-host persistence (LAN sweep results) ─────────────────────

  /// The production store for LAN-sweep DISCOVERED hosts — a JSON string list,
  /// most-recent first, capped at [kMaxDiscoveredHosts]. Named so tests can
  /// restore it after swapping in an in-memory one.
  static const BaseUrlOverrideStore defaultDiscoveredStore =
      SecureBaseUrlOverrideStore(storageKey: 'settings.discovered_hosts');

  /// Swappable for an in-memory store in tests.
  static BaseUrlOverrideStore discoveredStore = defaultDiscoveredStore;

  /// The persisted discovered hosts (normalized, most-recent first). Never
  /// throws — a missing/corrupt store reads as empty.
  static Future<List<String>> loadDiscovered() async {
    try {
      final raw = await discoveredStore.load();
      if (raw == null) return const [];
      final decoded = jsonDecode(raw);
      if (decoded is! List) return const [];
      return [
        for (final e in decoded)
          if (e is String && e.trim().isNotEmpty) normalizeBaseUrl(e),
      ];
    } catch (_) {
      return const [];
    }
  }

  /// Record [url] as a discovered gateway host: registered as a server alias
  /// IMMEDIATELY (so the switch to it cannot force a re-login) and persisted
  /// most-recent-first for future resolutions. After a successful save the
  /// dynamic alias set is reconciled to EXACTLY the capped persisted list, so
  /// a host evicted from the cap also stops being an alias (the registry must
  /// honor the same cap as the store). Best-effort — never throws.
  static Future<void> rememberDiscovered(String url) async {
    final n = normalizeBaseUrl(url);
    ServerAliasRegistry.add(n);
    try {
      final list = await loadDiscovered();
      final capped =
          [n, ...list.where((u) => u != n)].take(kMaxDiscoveredHosts).toList();
      await discoveredStore.save(jsonEncode(capped));
      ServerAliasRegistry.syncDynamic(capped);
    } catch (_) {
      // Persistence is an optimization for future launches; a failure here
      // must never disrupt the resolution that just succeeded — the add()
      // above already covers THIS session.
    }
  }

  /// Startup seed: register every persisted discovered host as a server alias
  /// so the SYNCHRONOUS alias checks (offline auth cache) know them before the
  /// first resolution runs. Called from `main()`; cheap (one storage read).
  static Future<void> seedDynamicAliases() async {
    ServerAliasRegistry.seed(await loadDiscovered());
  }

  /// The production LAN sweep, or `null` when not wired. `main()` sets this to
  /// [LanDiscovery.sweepAllInterfaces] at startup (the composition root), so
  /// unit tests — which never run `main()` — can never fire a real network
  /// sweep by accident.
  static LanSweep? lanSweep;

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
  /// 1. [kDefaultBaseUrl] — the public Tailscale Funnel front door. Works on
  ///    cellular and away from home (outbound tunnel — CGNAT/VPN can't block it).
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

  /// Back-compat wrapper around [resolveGateway] — returns only the URL.
  static Future<String> resolveBaseUrl({HealthProbe? probe}) async =>
      (await resolveGateway(probe: probe)).url;

  /// Resolves the gateway to use, reporting whether a host actually answered.
  ///
  /// 1. If a manual OVERRIDE is set, it is probed FIRST, alone. When reachable
  ///    it's returned — the user's explicit choice wins.
  /// 2. CONCRETE candidates (IP / DuckDNS — reliable) are probed as ONE
  ///    PARALLEL batch (worst case costs one probe timeout, not one per host)
  ///    and the highest-priority host that answered wins:
  ///      last-known-good → DuckDNS front door → pinned LAN IP → persisted
  ///      DISCOVERED hosts → USB loopback ([kUsbLoopbackBaseUrl], `adb reverse`).
  /// 3. If no concrete host answers and a LAN sweep is wired ([lanSweep], or
  ///    the [sweep] parameter in tests), the sweep hunts the phone's own
  ///    subnets for the gateway — the recovery path for "DHCP moved the Mac".
  ///    A find is adopted, persisted via [rememberDiscovered], returned
  ///    reachable.
  /// 4. mDNS `.local` hosts ([kLanFallbackBaseUrl]) are the ABSOLUTE last
  ///    resort — probed only after concrete candidates AND the sweep fail.
  ///    Dart's `HttpClient` on Android resolves `.local` only intermittently,
  ///    so a `.local` that "answers" one probe then fails every real request
  ///    must NEVER win over — or suppress the discovery of — a concrete IP.
  ///    (2026-07-20 stuck-offline incident: the Mac's IP moved, the app
  ///    latched onto flaky `.local`, and the sweep never ran because `.local`
  ///    had "answered".)
  /// 5. Otherwise the fail-safe: the override if set (explicit choice
  ///    preserved, diagnosable via [probeAll]), else [kDefaultBaseUrl] —
  ///    flagged `reachable: false` so callers don't persist it.
  ///
  /// A dead override never bricks the app (the login screen has no server-URL
  /// field to escape it): it is only set aside when a DIFFERENT host answers.
  static Future<ResolvedGateway> resolveGateway({
    HealthProbe? probe,
    LanSweep? sweep,
  }) async {
    final check = probe ?? _defaultProbe;
    final doSweep = sweep ?? lanSweep;

    final override = await loadOverride();
    if (override != null && await _safeCheck(check, override)) {
      return (url: override, reachable: true);
    }

    // Partition every candidate into CONCRETE (IP/DuckDNS) and MDNS (`.local`).
    // Concrete always outranks mDNS AND the sweep runs between them, so a
    // moved-IP scenario resolves to a reliable IP, never a flaky `.local`.
    final lastGood = await loadLastResolved();
    final discovered = await loadDiscovered();
    final concrete = <String>[];
    final mdns = <String>[];
    final seen = <String>{if (override != null) normalizeBaseUrl(override)};
    void addCandidate(String url) {
      final n = normalizeBaseUrl(url);
      if (!seen.add(n)) return;
      (_isMdnsHost(n) ? mdns : concrete).add(n);
    }

    if (lastGood != null) addCandidate(lastGood);
    _candidates.forEach(addCandidate);
    discovered.forEach(addCandidate);
    addCandidate(kUsbLoopbackBaseUrl);

    // 1) Concrete candidates — parallel probe, highest-priority answerer wins.
    final concreteHit = await _firstReachable(check, concrete);
    if (concreteHit != null) return (url: concreteHit, reachable: true);

    // 2) LAN sweep — finds the current concrete IP when everything moved.
    if (doSweep != null) {
      try {
        final found = await doSweep();
        if (found.isNotEmpty) {
          final url = normalizeBaseUrl(found.first);
          await rememberDiscovered(url);
          return (url: url, reachable: true);
        }
      } catch (_) {
        // A failing sweep must never break resolution — fall through.
      }
    }

    // 3) mDNS `.local` — absolute last resort (see step 4 in the doc above).
    final mdnsHit = await _firstReachable(check, mdns);
    if (mdnsHit != null) return (url: mdnsHit, reachable: true);

    // 4) Fail-safe — override if set, else default; flagged unreachable.
    return (url: override ?? kDefaultBaseUrl, reachable: false);
  }

  /// Probes [urls] concurrently and returns the FIRST (highest-priority) that
  /// answers, or `null` when none do. Probes start together but are awaited in
  /// order, so the call returns as soon as the top host answers without waiting
  /// for slower losers to time out ([_safeCheck] swallows their late failures).
  static Future<String?> _firstReachable(
      HealthProbe check, List<String> urls) async {
    final answering = [for (final url in urls) _safeCheck(check, url)];
    for (var i = 0; i < urls.length; i++) {
      if (await answering[i]) return urls[i];
    }
    return null;
  }

  /// True when [url]'s host is an mDNS `.local` name — unreliable to resolve in
  /// Dart's `HttpClient` on Android, hence demoted to last-resort in
  /// [resolveGateway]. Never throws (a malformed URL is treated as concrete).
  static bool _isMdnsHost(String url) {
    try {
      return Uri.parse(url).host.toLowerCase().endsWith('.local');
    } catch (_) {
      return false;
    }
  }

  /// Runs [check] on [url], swallowing sync AND async probe exceptions into
  /// `false` so one exploding probe can never abort the whole resolution.
  static Future<bool> _safeCheck(HealthProbe check, String url) async {
    try {
      return await check(url);
    } catch (_) {
      return false;
    }
  }

  /// Probes EVERY known gateway (the override, if set, then the auto
  /// candidates) and returns each with its reachability. Used by the Settings
  /// "Test connection" diagnostics so a silent-failure ("phone can't reach
  /// 192.168.0.12") becomes visible. Duplicates (an override equal to a
  /// candidate) are collapsed to a single entry.
  static Future<List<GatewayStatus>> probeAll({HealthProbe? probe}) async {
    final check = probe ?? _defaultProbe;
    final override = await loadOverride();
    final lastGood = await loadLastResolved();
    final discovered = await loadDiscovered();

    final planned = <({String url, String label})>[
      if (override != null) (url: override, label: 'Custom (saved)'),
      (url: kDefaultBaseUrl, label: 'Remote (Funnel)'),
      (url: kLanFallbackBaseUrl, label: 'LAN (mDNS)'),
      (url: kLanFallbackIpBaseUrl, label: 'LAN (IP)'),
      (url: kUsbLoopbackBaseUrl, label: 'USB (adb reverse)'),
      if (lastGood != null) (url: lastGood, label: 'Last known good'),
      for (final url in discovered) (url: url, label: 'Discovered (LAN scan)'),
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
