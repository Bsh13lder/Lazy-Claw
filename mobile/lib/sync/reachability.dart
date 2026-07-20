import 'dart:async';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';

import '../core/api/api_client.dart';

/// Thin seam over the bits Reachability needs, so it can be unit-tested with a
/// fake (no real radios, no real HTTP).
abstract class ConnectivityProbe {
  /// Emits whenever the OS connectivity changes.
  Stream<bool> get onChanged;

  /// One-shot "does the OS think we have a link right now?".
  Future<bool> hasLink();

  /// Active health ping to the user's own computer. Internet != the box being
  /// up, so we hit the UNAUTHENTICATED `GET {baseUrl}/api/health` — "reachable"
  /// means the server answers, independent of login state. (The old
  /// `/api/system/about` needs auth and 401s before/without a valid session,
  /// which wrongly read as "offline" even while the app was connected.)
  Future<bool> pingHost();
}

/// Production probe: `connectivity_plus` for the OS link + a real HTTP ping.
class DefaultConnectivityProbe implements ConnectivityProbe {
  final Connectivity _connectivity;
  final ApiClient _client;

  DefaultConnectivityProbe(this._client, {Connectivity? connectivity})
      : _connectivity = connectivity ?? Connectivity();

  @override
  Stream<bool> get onChanged =>
      _connectivity.onConnectivityChanged.map(_hasAnyLink);

  @override
  Future<bool> hasLink() async {
    final result = await _connectivity.checkConnectivity();
    return _hasAnyLink(result);
  }

  @override
  Future<bool> pingHost() async {
    try {
      // Unauthenticated + always-2xx when the gateway is up. Do NOT use an
      // authed endpoint here — a 401 means "up but not logged in", not "offline".
      await _client.get<dynamic>('/api/health');
      return true;
    } catch (_) {
      return false;
    }
  }

  static bool _hasAnyLink(List<ConnectivityResult> results) =>
      results.any((r) => r != ConnectivityResult.none);
}

/// Tracks whether the user's backend is reachable RIGHT NOW.
///
/// Reachable = the OS reports a link AND the active health ping to the host
/// succeeds. Exposes a broadcast [reachable] stream plus the latest [value].
/// The Tasks layer flips a sync on the false→true edge.
class Reachability {
  final ConnectivityProbe _probe;
  final _controller = StreamController<bool>.broadcast();
  StreamSubscription<bool>? _sub;

  bool _value = false;
  bool _started = false;

  Reachability(this._probe);

  /// Latest known reachability. Defaults to false until [start] runs a probe.
  bool get value => _value;

  /// Broadcast stream of reachability transitions.
  Stream<bool> get reachable => _controller.stream;

  /// Begin watching OS connectivity + run an initial host ping. Idempotent.
  Future<void> start() async {
    if (_started) return;
    _started = true;
    _sub = _probe.onChanged.listen((_) => refresh());
    await refresh();
  }

  /// Re-evaluate reachability now and emit on the stream only when the value
  /// actually changes.
  ///
  /// The `/api/health` ping is AUTHORITATIVE: if our backend answers, we ARE
  /// online — full stop. We deliberately do NOT gate the ping behind
  /// `connectivity_plus`'s `hasLink()`. That heuristic false-NEGATIVES on some
  /// Android ROMs / hotspot / VPN configs, and gating the ping behind it
  /// stranded the app "offline" (Home badge + sync frozen) even while the chat
  /// WebSocket — which never consults `hasLink()` — was connected to the very
  /// same host (2026-07-20 incident). The OS link state still drives WHEN we
  /// re-check (the [onChanged] subscription), but never vetoes a server that
  /// actually responds. When genuinely offline the ping just fails fast → still
  /// correctly offline.
  Future<bool> refresh() async {
    final next = await _probe.pingHost();
    _set(next);
    return next;
  }

  void _set(bool next) {
    final changed = next != _value;
    if (changed) {
      debugPrint(
        'Reachability: transition ${_value ? 'online' : 'offline'} → '
        '${next ? 'online' : 'offline'} (host reachable=$next)',
      );
    }
    _value = next;
    if (changed) _controller.add(next);
  }

  Future<void> dispose() async {
    await _sub?.cancel();
    await _controller.close();
  }
}
