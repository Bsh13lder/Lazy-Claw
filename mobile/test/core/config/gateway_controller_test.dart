// Covers the runtime-switchable gateway wiring:
//   * [GatewayController.reresolve] switches the active URL when a DIFFERENT
//     candidate is now first-reachable (honoring any override), and is a no-op
//     when the resolved URL is unchanged;
//   * [GatewayController.setManual] persists the override AND flips the active
//     URL immediately;
//   * [GatewayController.clearManual] drops the override and re-resolves;
//   * the active URL flows through: [baseUrlProvider] mirrors it and
//     [apiClientProvider] REBUILDS (new ApiClient with the new baseUrl) when it
//     changes — the crux of "the app was pinned to a dead URL forever".
//
// Everything is exercised through a [ProviderContainer] (no widgets — no live
// notifier pump), with an in-memory override store + injected probes so there
// is no real network or secure-storage I/O.

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/config/base_url_override_store.dart';
import 'package:lazyclaw_mobile/core/config/server_config.dart';
import 'package:lazyclaw_mobile/core/constants/app_constants.dart';
import 'package:lazyclaw_mobile/providers/auth_provider.dart';
import 'package:lazyclaw_mobile/providers/gateway_provider.dart';

ProviderContainer _makeContainer({String seed = kDefaultBaseUrl}) {
  final container = ProviderContainer(overrides: [
    bootstrapBaseUrlProvider.overrideWithValue(seed),
  ]);
  addTearDown(container.dispose);
  return container;
}

void main() {
  setUp(() {
    ServerConfig.overrideStore = InMemoryBaseUrlOverrideStore();
    ServerConfig.lastResolvedStore = InMemoryBaseUrlOverrideStore();
  });
  tearDown(() {
    ServerConfig.overrideStore = const SecureBaseUrlOverrideStore();
    ServerConfig.lastResolvedStore = ServerConfig.defaultLastResolvedStore;
  });

  test('activeBaseUrlProvider seeds from the bootstrap URL', () {
    final c = _makeContainer(seed: 'http://192.168.0.7:18789');
    expect(c.read(activeBaseUrlProvider), 'http://192.168.0.7:18789');
  });

  test('baseUrlProvider mirrors the active URL', () {
    final c = _makeContainer(seed: 'http://192.168.0.7:18789');
    expect(c.read(baseUrlProvider), 'http://192.168.0.7:18789');
  });

  test('reresolve switches when a different candidate is first-reachable',
      () async {
    final c = _makeContainer();
    // Primary is dead; only the mDNS LAN host answers now.
    await c.read(activeBaseUrlProvider.notifier).reresolve(
          probe: (b) async => b == kLanFallbackBaseUrl,
        );
    expect(c.read(activeBaseUrlProvider), kLanFallbackBaseUrl);
    expect(c.read(baseUrlProvider), kLanFallbackBaseUrl);
  });

  test('reresolve is a no-op when the resolved URL is unchanged', () async {
    final c = _makeContainer();
    var switches = 0;
    c.listen(activeBaseUrlProvider, (_, _) => switches++);
    // Primary still first-reachable → resolves to the same seed → no change.
    await c
        .read(activeBaseUrlProvider.notifier)
        .reresolve(probe: (b) async => true);
    expect(c.read(activeBaseUrlProvider), kDefaultBaseUrl);
    expect(switches, 0);
  });

  test('reresolve persists the resolved host as the last-known-good seed',
      () async {
    // So the NEXT cold start seeds directly to the host that just answered,
    // avoiding a background URL switch (and the auth rebuild it triggers).
    final c = _makeContainer();
    await c.read(activeBaseUrlProvider.notifier).reresolve(
          probe: (b) async => b == kLanFallbackIpBaseUrl,
        );
    expect(await ServerConfig.loadLastResolved(), kLanFallbackIpBaseUrl);
  });

  test(
      'reresolve does NOT overwrite the last-known-good seed when nothing '
      'answers (the unreachable fail-safe must not clobber a good seed)',
      () async {
    // Regression: reresolve used to remember WHATEVER resolution returned —
    // including the fail-safe default when every candidate was dead — which
    // overwrote a genuinely-working remembered LAN host and made the NEXT
    // cold start seed to an unreachable URL.
    await ServerConfig.rememberResolved('http://192.168.0.15:18789');
    final c = _makeContainer();
    await c
        .read(activeBaseUrlProvider.notifier)
        .reresolve(probe: (b) async => false);
    expect(await ServerConfig.loadLastResolved(), 'http://192.168.0.15:18789');
  });

  test('overlapping reresolve calls coalesce into ONE resolution', () async {
    // reresolve fires from two un-synchronized sites (cold-start post-frame +
    // every app resume). Overlapping calls must share one in-flight resolution
    // — otherwise both can reach the "nothing answered" branch and stack TWO
    // concurrent 48-socket LAN sweeps (and race the discovered-store write).
    final c = _makeContainer();
    var defaultProbes = 0;
    Future<void> call() => c.read(activeBaseUrlProvider.notifier).reresolve(
          probe: (b) async {
            if (b == kDefaultBaseUrl) defaultProbes++;
            await Future<void>.delayed(const Duration(milliseconds: 50));
            return true;
          },
        );
    await Future.wait([call(), call()]);
    expect(defaultProbes, 1,
        reason: 'the second call must piggyback on the in-flight resolution');

    // A LATER call (no overlap) runs a fresh resolution again.
    await call();
    expect(defaultProbes, 2);
  });

  test('reresolve honors a reachable override', () async {
    ServerConfig.overrideStore =
        InMemoryBaseUrlOverrideStore('http://box.lan:9000');
    final c = _makeContainer();
    await c
        .read(activeBaseUrlProvider.notifier)
        .reresolve(probe: (b) async => true);
    expect(c.read(activeBaseUrlProvider), 'http://box.lan:9000');
  });

  test('setManual persists the (normalized) override and flips the active URL',
      () async {
    final c = _makeContainer();
    await c.read(activeBaseUrlProvider.notifier).setManual('192.168.0.99:18789');
    expect(c.read(activeBaseUrlProvider), 'http://192.168.0.99:18789');
    expect(await ServerConfig.loadOverride(), 'http://192.168.0.99:18789');
  });

  test('clearManual drops the override and re-resolves automatically',
      () async {
    ServerConfig.overrideStore =
        InMemoryBaseUrlOverrideStore('http://box.lan:9000');
    final c = _makeContainer(seed: 'http://box.lan:9000');
    // Sanity: starts on the override.
    expect(c.read(activeBaseUrlProvider), 'http://box.lan:9000');

    await c.read(activeBaseUrlProvider.notifier).clearManual(
          probe: (b) async => b == kLanFallbackIpBaseUrl,
        );
    expect(await ServerConfig.loadOverride(), isNull);
    expect(c.read(activeBaseUrlProvider), kLanFallbackIpBaseUrl);
  });

  test('apiClientProvider REBUILDS with the new baseUrl when the active URL flips',
      () async {
    final c = _makeContainer();
    // Keep the provider subscribed so we observe the rebuild.
    c.listen(apiClientProvider, (_, _) {}, fireImmediately: true);

    final before = c.read(apiClientProvider);
    expect(before.baseUrl, kDefaultBaseUrl);

    await c
        .read(activeBaseUrlProvider.notifier)
        .setManual('http://192.168.0.99:18789');

    final after = c.read(apiClientProvider);
    expect(after.baseUrl, 'http://192.168.0.99:18789');
    expect(identical(before, after), isFalse, reason: 'a NEW client is built');
  });
}
