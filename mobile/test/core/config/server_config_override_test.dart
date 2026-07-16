// Covers the manual base-URL OVERRIDE surface added to [ServerConfig]:
//   * override persistence round-trips (save → load → clear), normalized;
//   * resolveBaseUrl PREFERS a reachable override over the auto candidates;
//   * an explicitly-set override that is UNREACHABLE is STILL returned (the
//     user's explicit choice is never silently discarded — it's diagnosable via
//     probeAll instead);
//   * with no override, resolveBaseUrl keeps the existing auto candidate order;
//   * probeAll reports per-candidate reachability (incl. the override) using an
//     injected probe — no real network.
//
// All I/O is faked: an [InMemoryBaseUrlOverrideStore] stands in for secure
// storage, and every reachability read goes through an injected [HealthProbe].

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/config/base_url_override_store.dart';
import 'package:lazyclaw_mobile/core/config/server_config.dart';
import 'package:lazyclaw_mobile/core/constants/app_constants.dart';

void main() {
  setUp(() {
    ServerConfig.overrideStore = InMemoryBaseUrlOverrideStore();
    ServerConfig.lastResolvedStore = InMemoryBaseUrlOverrideStore();
  });
  tearDown(() {
    // Restore the production stores so no cross-test bleed occurs.
    ServerConfig.overrideStore = const SecureBaseUrlOverrideStore();
    ServerConfig.lastResolvedStore = ServerConfig.defaultLastResolvedStore;
  });

  group('override persistence', () {
    test('round-trips: absent → save (normalized) → load → clear', () async {
      expect(await ServerConfig.loadOverride(), isNull);

      await ServerConfig.saveOverride('192.168.0.50:18789');
      expect(await ServerConfig.loadOverride(), 'http://192.168.0.50:18789');

      await ServerConfig.clearOverride();
      expect(await ServerConfig.loadOverride(), isNull);
    });

    test('an empty / whitespace value in the store reads back as null',
        () async {
      // The UI must reject empty input before saving; loadOverride is the
      // defensive guard so a blank stored value never becomes an active URL.
      ServerConfig.overrideStore = InMemoryBaseUrlOverrideStore('   ');
      expect(await ServerConfig.loadOverride(), isNull);
    });

    test('loadOverride never throws — a failing store resolves to null',
        () async {
      ServerConfig.overrideStore = _ThrowingOverrideStore();
      expect(await ServerConfig.loadOverride(), isNull);
    });
  });

  group('resolveBaseUrl with override', () {
    test('reachable override wins over reachable auto candidates', () async {
      ServerConfig.overrideStore =
          InMemoryBaseUrlOverrideStore('http://box.lan:9000');
      final probed = <String>[];
      final url = await ServerConfig.resolveBaseUrl(probe: (b) async {
        probed.add(b);
        return true; // everything is reachable
      });
      expect(url, 'http://box.lan:9000');
      // Only the override is probed — the auto candidates are never reached.
      expect(probed, ['http://box.lan:9000']);
    });

    test(
        'unreachable override falls back to a reachable auto candidate '
        '(a dead override must never brick the app)', () async {
      // Regression: a manual override pinned to a since-vanished LAN IP (e.g.
      // an ephemeral dock/ethernet address that lost its DHCP lease) used to be
      // returned UNCONDITIONALLY — which bricks the login screen, since that
      // screen has no server-URL field to escape it. Now a dead override yields
      // to any auto candidate that actually answers, so the app self-heals to a
      // reachable LAN host.
      ServerConfig.overrideStore =
          InMemoryBaseUrlOverrideStore('http://192.168.0.18:18789');
      final probed = <String>[];
      final url = await ServerConfig.resolveBaseUrl(probe: (b) async {
        probed.add(b);
        return b == kLanFallbackIpBaseUrl; // only the Mac's LAN IP answers
      });
      expect(url, kLanFallbackIpBaseUrl);
      // The dead override is probed FIRST, then we fall through to candidates.
      expect(probed.first, 'http://192.168.0.18:18789');
      expect(probed.contains(kLanFallbackIpBaseUrl), isTrue);
    });

    test(
        'unreachable override with NOTHING else reachable is STILL returned '
        '(explicit choice preserved as a last resort, diagnosable via probeAll)',
        () async {
      ServerConfig.overrideStore =
          InMemoryBaseUrlOverrideStore('http://box.lan:9000');
      final url = await ServerConfig.resolveBaseUrl(probe: (b) async => false);
      expect(url, 'http://box.lan:9000');
    });

    test('no override → falls back to the auto candidate order', () async {
      final url = await ServerConfig.resolveBaseUrl(
        probe: (b) async => b == kLanFallbackBaseUrl,
      );
      expect(url, kLanFallbackBaseUrl);
    });

    test('no override, nothing reachable → fail-safe to primary', () async {
      final url = await ServerConfig.resolveBaseUrl(probe: (b) async => false);
      expect(url, kDefaultBaseUrl);
    });
  });

  group('seedBaseUrl (non-blocking startup seed)', () {
    // The startup seed must NEVER touch the network — it exists so `main()` can
    // render the first frame instantly instead of blocking on the 3s-per-host
    // reachability probes. Reachability resolution is deferred to a background
    // `GatewayController.reresolve()` after the first frame. These tests pin the
    // "no probe, just the cheap saved-override-or-default" contract.
    test('returns the saved override when one is set (no network)', () async {
      ServerConfig.overrideStore =
          InMemoryBaseUrlOverrideStore('http://box.lan:9000');
      expect(await ServerConfig.seedBaseUrl(), 'http://box.lan:9000');
    });

    test('returns kDefaultBaseUrl when no override and no last-known-good',
        () async {
      expect(await ServerConfig.seedBaseUrl(), kDefaultBaseUrl);
    });

    test('falls back to the last-known-good host when no override is set',
        () async {
      // The host that answered last time is remembered so the NEXT cold start
      // seeds to it directly — the common home-WiFi repeat launch then resolves
      // to the SAME host in the background (no URL switch → no auth rebuild).
      await ServerConfig.rememberResolved(kLanFallbackIpBaseUrl);
      expect(await ServerConfig.seedBaseUrl(), kLanFallbackIpBaseUrl);
    });

    test('an explicit override still wins over the last-known-good', () async {
      ServerConfig.overrideStore =
          InMemoryBaseUrlOverrideStore('http://box.lan:9000');
      await ServerConfig.rememberResolved(kLanFallbackIpBaseUrl);
      expect(await ServerConfig.seedBaseUrl(), 'http://box.lan:9000');
    });

    test('a whitespace-only stored override seeds the default, not blank',
        () async {
      ServerConfig.overrideStore = InMemoryBaseUrlOverrideStore('   ');
      expect(await ServerConfig.seedBaseUrl(), kDefaultBaseUrl);
    });

    test('never throws — a failing store seeds the default', () async {
      ServerConfig.overrideStore = _ThrowingOverrideStore();
      expect(await ServerConfig.seedBaseUrl(), kDefaultBaseUrl);
    });

    test('a failing last-known-good store seeds the default (never throws)',
        () async {
      ServerConfig.lastResolvedStore = _ThrowingOverrideStore();
      expect(await ServerConfig.seedBaseUrl(), kDefaultBaseUrl);
    });
  });

  group('last-known-good persistence', () {
    test('rememberResolved round-trips through loadLastResolved', () async {
      expect(await ServerConfig.loadLastResolved(), isNull);
      await ServerConfig.rememberResolved(kLanFallbackIpBaseUrl);
      expect(await ServerConfig.loadLastResolved(), kLanFallbackIpBaseUrl);
    });

    test('loadLastResolved never throws — a failing store resolves to null',
        () async {
      ServerConfig.lastResolvedStore = _ThrowingOverrideStore();
      expect(await ServerConfig.loadLastResolved(), isNull);
    });
  });

  group('probeAll', () {
    test('no override → the three auto candidates, each with its reachability',
        () async {
      final statuses = await ServerConfig.probeAll(
        probe: (b) async => b == kLanFallbackIpBaseUrl,
      );
      expect(statuses.map((s) => s.url).toList(),
          [kDefaultBaseUrl, kLanFallbackBaseUrl, kLanFallbackIpBaseUrl]);
      expect(
        statuses.firstWhere((s) => s.url == kLanFallbackIpBaseUrl).reachable,
        isTrue,
      );
      expect(
        statuses.firstWhere((s) => s.url == kDefaultBaseUrl).reachable,
        isFalse,
      );
    });

    test('override is listed FIRST and probed', () async {
      ServerConfig.overrideStore =
          InMemoryBaseUrlOverrideStore('http://box.lan:9000');
      final statuses = await ServerConfig.probeAll(
        probe: (b) async => b == 'http://box.lan:9000',
      );
      expect(statuses.length, 4);
      expect(statuses.first.url, 'http://box.lan:9000');
      expect(statuses.first.reachable, isTrue);
    });

    test('override that equals an auto candidate is not duplicated', () async {
      ServerConfig.overrideStore =
          InMemoryBaseUrlOverrideStore(kDefaultBaseUrl);
      final statuses = await ServerConfig.probeAll(probe: (b) async => false);
      expect(statuses.length, 3);
      expect(statuses.map((s) => s.url).toSet().length, 3);
    });

    test('a probe that throws for a candidate is reported unreachable',
        () async {
      final statuses = await ServerConfig.probeAll(
        probe: (b) async =>
            b == kDefaultBaseUrl ? throw StateError('boom') : true,
      );
      expect(
        statuses.firstWhere((s) => s.url == kDefaultBaseUrl).reachable,
        isFalse,
      );
      expect(
        statuses.firstWhere((s) => s.url == kLanFallbackBaseUrl).reachable,
        isTrue,
      );
    });
  });
}

/// An override store whose reads always throw — proves [ServerConfig.loadOverride]
/// swallows storage failures into `null`.
class _ThrowingOverrideStore implements BaseUrlOverrideStore {
  @override
  Future<String?> load() async => throw StateError('storage down');
  @override
  Future<void> save(String url) async {}
  @override
  Future<void> clear() async {}
}
