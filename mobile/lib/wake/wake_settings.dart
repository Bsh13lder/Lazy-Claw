/// Opt-in "Hey Lazy hotword" toggle. OFF by default. Enabling arms the wake
/// service; disabling stops it. Persisted so it survives restarts (the service
/// itself is re-armed on next foreground — Android 14/15 block silent boot resume).
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'wake_service.dart';

/// Tiny persistence seam so the toggle is unit-testable without secure storage.
abstract interface class WakeStore {
  Future<String?> read();
  Future<void> write(String value);
}

class SecureWakeStore implements WakeStore {
  static const _key = 'wake.enabled';
  final FlutterSecureStorage _s;
  const SecureWakeStore([this._s = const FlutterSecureStorage()]);
  @override
  Future<String?> read() => _s.read(key: _key);
  @override
  Future<void> write(String value) => _s.write(key: _key, value: value);
}

class WakeEnabledController extends StateNotifier<bool> {
  WakeEnabledController(this._service, this._store) : super(false);
  final WakeService _service;
  final WakeStore _store;

  @override
  bool get debugState => state;

  /// Loads the persisted preference (does NOT auto-start — the app re-arms the
  /// service on foreground when this is true).
  Future<void> restore() async {
    final v = await _store.read();
    state = v == 'true';
  }

  /// Loads the preference AND re-arms the service if it was enabled but isn't
  /// running — the path back after a reboot (Android 14/15 block silent boot
  /// resume of a mic foreground service, so we re-arm on next foreground).
  Future<void> restoreAndRearm() async {
    await restore();
    if (state && !await _service.isRunning()) {
      final ok = await _service.start();
      if (!ok) state = false; // mic revoked after reboot → reflect reality
    }
  }

  Future<void> set(bool enabled) async {
    state = enabled;
    await _store.write(enabled ? 'true' : 'false');
    if (enabled) {
      final ok = await _service.start();
      if (!ok) state = false; // could not arm (mic denied) → reflect reality
    } else {
      await _service.stop();
    }
  }
}
