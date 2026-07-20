// Covers the DISCOVERY upgrade to [ServerConfig] resolution:
//   * the last-known-good host is a real probe candidate (previously it was
//     only a startup seed — resolution would abandon a working discovered IP
//     because it never probed it);
//   * persisted DISCOVERED hosts (earlier LAN-sweep finds) are probed;
//   * the USB loopback (`adb reverse`) host is probed as a last-resort
//     candidate;
//   * when NO candidate answers, the injected LAN sweep runs; its find is
//     adopted, persisted to the discovered store and registered as a server
//     alias;
//   * the sweep does NOT run when any candidate answers, and a missing/failing
//     sweep degrades to the old fail-safe behavior;
//   * [resolveGateway] reports reachability so the controller can avoid
//     remembering an unreachable fail-safe as "last known good".
//
// All I/O is faked: in-memory stores + injected probe/sweep. No network.

import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/config/base_url_override_store.dart';
import 'package:lazyclaw_mobile/core/config/server_aliases.dart';
import 'package:lazyclaw_mobile/core/config/server_config.dart';
import 'package:lazyclaw_mobile/core/constants/app_constants.dart';

void main() {
  setUp(() {
    ServerConfig.overrideStore = InMemoryBaseUrlOverrideStore();
    ServerConfig.lastResolvedStore = InMemoryBaseUrlOverrideStore();
    ServerConfig.discoveredStore = InMemoryBaseUrlOverrideStore();
    ServerAliasRegistry.resetForTests();
  });
  tearDown(() {
    ServerConfig.overrideStore = const SecureBaseUrlOverrideStore();
    ServerConfig.lastResolvedStore = ServerConfig.defaultLastResolvedStore;
    ServerConfig.discoveredStore = ServerConfig.defaultDiscoveredStore;
    ServerAliasRegistry.resetForTests();
  });

  group('discovered-host persistence', () {
    test('rememberDiscovered round-trips (normalized, most-recent first)',
        () async {
      expect(await ServerConfig.loadDiscovered(), isEmpty);
      await ServerConfig.rememberDiscovered('192.168.0.23:18789');
      await ServerConfig.rememberDiscovered('http://192.168.230.101:18789');
      expect(await ServerConfig.loadDiscovered(),
          ['http://192.168.230.101:18789', 'http://192.168.0.23:18789']);
    });

    test('re-remembering an existing host moves it to the front', () async {
      await ServerConfig.rememberDiscovered('http://192.168.0.23:18789');
      await ServerConfig.rememberDiscovered('http://192.168.230.101:18789');
      await ServerConfig.rememberDiscovered('http://192.168.0.23:18789');
      expect(await ServerConfig.loadDiscovered(),
          ['http://192.168.0.23:18789', 'http://192.168.230.101:18789']);
    });

    test('the list is capped at $kMaxDiscoveredHosts entries', () async {
      for (var i = 1; i <= kMaxDiscoveredHosts + 3; i++) {
        await ServerConfig.rememberDiscovered('http://192.168.0.$i:18789');
      }
      final list = await ServerConfig.loadDiscovered();
      expect(list.length, kMaxDiscoveredHosts);
      // Most recent survives, oldest fell off.
      expect(list.first,
          'http://192.168.0.${kMaxDiscoveredHosts + 3}:18789');
    });

    test('rememberDiscovered registers the host as a server alias', () async {
      await ServerConfig.rememberDiscovered('http://192.168.0.23:18789');
      expect(ServerAliasRegistry.isAlias('http://192.168.0.23:18789'), isTrue);
    });

    test('a host evicted from the discovered cap stops being a DYNAMIC alias',
        () async {
      // The alias registry must honor the same cap as the persisted store —
      // otherwise a long-running session accretes stale aliases the persisted
      // list already dropped.
      for (var i = 1; i <= kMaxDiscoveredHosts + 1; i++) {
        await ServerConfig.rememberDiscovered('http://10.0.0.$i:18789');
      }
      expect(ServerAliasRegistry.isAlias('http://10.0.0.1:18789'), isFalse,
          reason: 'evicted from the capped store → no longer an alias');
      expect(ServerAliasRegistry.isAlias('http://10.0.0.2:18789'), isTrue,
          reason: 'still in the capped store → still an alias');
    });

    test('loadDiscovered never throws — corrupt store reads as empty',
        () async {
      ServerConfig.discoveredStore =
          InMemoryBaseUrlOverrideStore('not-json-at-all{{{');
      expect(await ServerConfig.loadDiscovered(), isEmpty);
    });

    test('seedDynamicAliases registers persisted discovered hosts', () async {
      await ServerConfig.rememberDiscovered('http://192.168.0.23:18789');
      ServerAliasRegistry.resetForTests();
      expect(
          ServerAliasRegistry.isAlias('http://192.168.0.23:18789'), isFalse);
      await ServerConfig.seedDynamicAliases();
      expect(ServerAliasRegistry.isAlias('http://192.168.0.23:18789'), isTrue);
    });
  });

  group('resolveGateway candidate ladder', () {
    test('the last-known-good host is probed and PREFERRED over the default',
        () async {
      await ServerConfig.rememberResolved('http://192.168.0.23:18789');
      final res = await ServerConfig.resolveGateway(probe: (b) async => true);
      expect(res.url, 'http://192.168.0.23:18789');
      expect(res.reachable, isTrue);
    });

    test('a persisted discovered host is probed and wins when only it answers',
        () async {
      await ServerConfig.rememberDiscovered('http://192.168.0.23:18789');
      final res = await ServerConfig.resolveGateway(
        probe: (b) async => b == 'http://192.168.0.23:18789',
      );
      expect(res.url, 'http://192.168.0.23:18789');
      expect(res.reachable, isTrue);
    });

    test('the USB loopback host is a last-resort candidate', () async {
      final res = await ServerConfig.resolveGateway(
        probe: (b) async => b == kUsbLoopbackBaseUrl,
      );
      expect(res.url, kUsbLoopbackBaseUrl);
      expect(res.reachable, isTrue);
    });

    test('candidate order is respected when several answer', () async {
      // Default answers AND loopback answers → the default outranks it.
      final res = await ServerConfig.resolveGateway(
        probe: (b) async =>
            b == kDefaultBaseUrl || b == kUsbLoopbackBaseUrl,
      );
      expect(res.url, kDefaultBaseUrl);
    });

    test('nothing reachable, no sweep wired → old fail-safe, NOT reachable',
        () async {
      final res = await ServerConfig.resolveGateway(probe: (b) async => false);
      expect(res.url, kDefaultBaseUrl);
      expect(res.reachable, isFalse);
    });

    test(
        'returns the priority winner WITHOUT waiting for slower candidates '
        '(a hung `.local` probe must not tax every resolution 3s)', () async {
      // The loopback probe NEVER completes; if resolution waited for the whole
      // batch (Future.wait) this would time out. Awaiting in priority order
      // returns as soon as the top candidate answers.
      final never = Completer<bool>();
      final res = await ServerConfig.resolveGateway(
        probe: (b) async =>
            b == kDefaultBaseUrl ? true : await never.future,
      ).timeout(const Duration(seconds: 2));
      expect(res.url, kDefaultBaseUrl);
      expect(res.reachable, isTrue);
    });
  });

  group('resolveGateway LAN sweep', () {
    test('runs only when NO candidate answers, adopts + persists the find',
        () async {
      var swept = false;
      final res = await ServerConfig.resolveGateway(
        probe: (b) async => false,
        sweep: () async {
          swept = true;
          return ['http://192.168.0.23:18789'];
        },
      );
      expect(swept, isTrue);
      expect(res.url, 'http://192.168.0.23:18789');
      expect(res.reachable, isTrue);
      // Persisted for future launches + registered as an alias so the
      // URL switch cannot force a re-login.
      expect(await ServerConfig.loadDiscovered(),
          contains('http://192.168.0.23:18789'));
      expect(ServerAliasRegistry.isAlias('http://192.168.0.23:18789'), isTrue);
    });

    test('does NOT run when a candidate answers', () async {
      var swept = false;
      final res = await ServerConfig.resolveGateway(
        probe: (b) async => b == kLanFallbackIpBaseUrl,
        sweep: () async {
          swept = true;
          return ['http://192.168.0.23:18789'];
        },
      );
      expect(swept, isFalse);
      expect(res.url, kLanFallbackIpBaseUrl);
    });

    test('an empty sweep falls back to the old fail-safe', () async {
      final res = await ServerConfig.resolveGateway(
        probe: (b) async => false,
        sweep: () async => [],
      );
      expect(res.url, kDefaultBaseUrl);
      expect(res.reachable, isFalse);
    });

    test('a throwing sweep falls back to the old fail-safe', () async {
      final res = await ServerConfig.resolveGateway(
        probe: (b) async => false,
        sweep: () async => throw StateError('sweep exploded'),
      );
      expect(res.url, kDefaultBaseUrl);
      expect(res.reachable, isFalse);
    });

    test('with a dead override set, the sweep find still wins (anti-brick)',
        () async {
      ServerConfig.overrideStore =
          InMemoryBaseUrlOverrideStore('http://192.168.0.18:18789');
      final res = await ServerConfig.resolveGateway(
        probe: (b) async => false,
        sweep: () async => ['http://192.168.0.23:18789'],
      );
      expect(res.url, 'http://192.168.0.23:18789');
      expect(res.reachable, isTrue);
    });

    test('with a dead override and an empty sweep, the override is preserved',
        () async {
      ServerConfig.overrideStore =
          InMemoryBaseUrlOverrideStore('http://box.lan:9000');
      final res = await ServerConfig.resolveGateway(
        probe: (b) async => false,
        sweep: () async => [],
      );
      expect(res.url, 'http://box.lan:9000');
      expect(res.reachable, isFalse);
    });
  });

  group('mDNS (.local) is last-resort — the 2026-07-20 stuck-offline bug', () {
    test(
        'a reachable .local does NOT suppress the sweep; the concrete swept IP '
        'wins (Dart can\'t reliably resolve .local, so a flaky mDNS host must '
        'never beat a real IP)', () async {
      final res = await ServerConfig.resolveGateway(
        // Only the mDNS name answers the probe...
        probe: (b) async => b == kLanFallbackBaseUrl,
        // ...but the sweep finds a concrete IP. The IP must win.
        sweep: () async => ['http://192.168.0.23:18789'],
      );
      expect(res.url, 'http://192.168.0.23:18789');
      expect(res.reachable, isTrue);
    });

    test('only .local reachable and the sweep is empty → .local as last resort',
        () async {
      final res = await ServerConfig.resolveGateway(
        probe: (b) async => b == kLanFallbackBaseUrl,
        sweep: () async => [],
      );
      expect(res.url, kLanFallbackBaseUrl);
      expect(res.reachable, isTrue);
    });

    test('a concrete candidate outranks a reachable .local', () async {
      // Both the mDNS name and the pinned LAN IP answer — the IP must win.
      final res = await ServerConfig.resolveGateway(
        probe: (b) async =>
            b == kLanFallbackBaseUrl || b == kLanFallbackIpBaseUrl,
      );
      expect(res.url, kLanFallbackIpBaseUrl);
    });

    test('a .local last-known-good never outranks a concrete candidate',
        () async {
      // Regression: the buggy build latched onto .local and remembered it as
      // last-known-good, so it kept winning. A concrete host must still beat a
      // remembered .local.
      await ServerConfig.rememberResolved(kLanFallbackBaseUrl);
      final res = await ServerConfig.resolveGateway(
        probe: (b) async =>
            b == kLanFallbackBaseUrl || b == kLanFallbackIpBaseUrl,
      );
      expect(res.url, kLanFallbackIpBaseUrl);
    });
  });
}
