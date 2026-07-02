import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Persistence seam for the user's MANUAL base-URL override.
///
/// Kept behind an interface so [ServerConfig] can be unit-tested with an
/// in-memory store (no secure-storage platform channel) and so the storage
/// backend can be swapped without touching resolution logic.
abstract class BaseUrlOverrideStore {
  /// The saved override, or `null` when none is set. Never throws for the
  /// production impl — see [SecureBaseUrlOverrideStore].
  Future<String?> load();

  /// Persist [url] as the override. Callers normalize before saving.
  Future<void> save(String url);

  /// Remove any saved override (back to automatic resolution).
  Future<void> clear();
}

/// Secure-storage-backed override store.
///
/// The manual URL is low-sensitivity, but we reuse [FlutterSecureStorage] so it
/// shares the app's existing secure-prefs mechanism (same as `SettingsPrefs`)
/// instead of introducing a second storage. Reads swallow storage errors into
/// `null` so a missing plugin (unit test) or a locked keystore can never crash
/// URL resolution.
class SecureBaseUrlOverrideStore implements BaseUrlOverrideStore {
  const SecureBaseUrlOverrideStore();

  /// Secure-storage key for the manual base URL.
  static const String key = 'settings.manual_base_url';

  static const FlutterSecureStorage _storage = FlutterSecureStorage();

  @override
  Future<String?> load() async {
    try {
      final v = await _storage.read(key: key);
      if (v == null) return null;
      final t = v.trim();
      return t.isEmpty ? null : t;
    } catch (_) {
      return null;
    }
  }

  @override
  Future<void> save(String url) => _storage.write(key: key, value: url);

  @override
  Future<void> clear() => _storage.delete(key: key);
}

/// In-memory override store for tests (and any transient, non-persistent use).
class InMemoryBaseUrlOverrideStore implements BaseUrlOverrideStore {
  InMemoryBaseUrlOverrideStore([this._value]);

  String? _value;

  @override
  Future<String?> load() async {
    final v = _value;
    if (v == null) return null;
    final t = v.trim();
    return t.isEmpty ? null : t;
  }

  @override
  Future<void> save(String url) async => _value = url;

  @override
  Future<void> clear() async => _value = null;
}
