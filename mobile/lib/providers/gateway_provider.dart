import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/config/server_config.dart';
import '../core/constants/app_constants.dart';

/// The URL resolved ONCE at startup (see `main.dart`), used only to SEED
/// [GatewayController]. Overridden in `main()` with the startup-resolved value;
/// the bare default keeps tests + a missing override sane.
final bootstrapBaseUrlProvider = Provider<String>((ref) => kDefaultBaseUrl);

/// Holds the ACTIVE base URL at runtime and is the single source of truth for
/// [baseUrlProvider] / `apiClientProvider`. Unlike the old one-shot frozen
/// `baseUrlProvider`, this can RE-RESOLVE at runtime — on app-resume or via the
/// Settings "reconnect" actions — so a phone that moved networks (or whose first
/// pick went dead) is never permanently pinned to an unreachable host.
class GatewayController extends StateNotifier<String> {
  GatewayController(super.initialUrl);

  /// Re-run [ServerConfig.resolveBaseUrl] (honoring any saved override) and
  /// switch the active URL only when a DIFFERENT candidate is now
  /// first-reachable. Cheap and idempotent — the common "same host still
  /// reachable" case mutates nothing (so it can't needlessly churn the API
  /// client / auth state).
  Future<void> reresolve({HealthProbe? probe}) async {
    final resolved = await ServerConfig.resolveBaseUrl(probe: probe);
    if (resolved != state) state = resolved;
    // Remember the host that answered so the NEXT cold start seeds straight to
    // it (see [ServerConfig.seedBaseUrl]). Fire-and-forget — a persistence
    // failure must never disrupt the live switch.
    unawaited(ServerConfig.rememberResolved(resolved));
  }

  /// Persist an explicit manual override and adopt it immediately. The URL is
  /// normalized before it is saved AND before it becomes the active URL, so the
  /// active value always matches what was stored.
  Future<void> setManual(String url) async {
    final normalized = ServerConfig.normalizeBaseUrl(url);
    await ServerConfig.saveOverride(normalized);
    if (state != normalized) state = normalized;
  }

  /// Clear the manual override and fall back to automatic resolution.
  Future<void> clearManual({HealthProbe? probe}) async {
    await ServerConfig.clearOverride();
    await reresolve(probe: probe);
  }
}

/// The runtime-switchable active base URL. Seeded from
/// [bootstrapBaseUrlProvider] (the startup-resolved value).
final activeBaseUrlProvider =
    StateNotifierProvider<GatewayController, String>((ref) {
  return GatewayController(ref.read(bootstrapBaseUrlProvider));
});
