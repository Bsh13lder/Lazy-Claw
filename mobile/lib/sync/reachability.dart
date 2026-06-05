import 'dart:async';

import 'package:connectivity_plus/connectivity_plus.dart';

import '../core/api/api_client.dart';

/// Thin seam over the bits Reachability needs, so it can be unit-tested with a
/// fake (no real radios, no real HTTP).
abstract class ConnectivityProbe {
  /// Emits whenever the OS connectivity changes.
  Stream<bool> get onChanged;

  /// One-shot "does the OS think we have a link right now?".
  Future<bool> hasLink();

  /// Active health ping to the user's own computer. Internet != the box being
  /// up, so we hit `GET {baseUrl}/api/system/about` and treat any 2xx/served
  /// response as reachable.
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
      await _client.get<dynamic>('/api/system/about');
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

  /// Re-evaluate reachability now (OS link gate, then active host ping) and
  /// emit on the stream only when the value actually changes.
  Future<bool> refresh() async {
    final hasLink = await _probe.hasLink();
    final next = hasLink ? await _probe.pingHost() : false;
    _set(next);
    return next;
  }

  void _set(bool next) {
    final changed = next != _value;
    _value = next;
    if (changed) _controller.add(next);
  }

  Future<void> dispose() async {
    await _sub?.cancel();
    await _controller.close();
  }
}
