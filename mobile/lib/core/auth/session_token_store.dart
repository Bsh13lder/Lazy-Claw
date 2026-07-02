import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Persistence seam for the session token (`session_id`) decoupled from any
/// hostname.
///
/// Why this exists: the server issues `session_id` as a plain cookie, and
/// [dio_cookie_manager]/[PersistCookieJar] key cookies by HOST. On a self-hosted
/// setup the very same server is reachable under several hostnames
/// (`192.168.0.12`, `<mac>.local`, the public DuckDNS name). When the app drifts
/// between those hosts, the jar refuses to send the cookie set under host A to
/// host B, so authenticated writes silently 401 while the unauthenticated health
/// check still reports "connected". Storing the token host-agnostically lets an
/// interceptor re-attach it to whichever host the app is currently using.
abstract class SessionTokenStore {
  /// The saved `session_id` value, or `null` when unset. Never throws for the
  /// production impl.
  Future<String?> load();

  /// Persist [token] as the current session id.
  Future<void> save(String token);

  /// Forget the session id (on logout / 401).
  Future<void> clear();
}

/// Secure-storage-backed session token store. Reuses [FlutterSecureStorage] so
/// the token lives alongside the app's other secure prefs. Reads swallow storage
/// errors into `null` so a locked keystore or a missing plugin (unit test) can
/// never crash request building.
class SecureSessionTokenStore implements SessionTokenStore {
  const SecureSessionTokenStore();

  /// Secure-storage key for the host-agnostic session id.
  static const String key = 'auth.session_id';

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
  Future<void> save(String token) => _storage.write(key: key, value: token);

  @override
  Future<void> clear() => _storage.delete(key: key);
}

/// In-memory store for tests (and any transient, non-persistent use).
class InMemorySessionTokenStore implements SessionTokenStore {
  InMemorySessionTokenStore([this._value]);

  String? _value;

  @override
  Future<String?> load() async {
    final v = _value;
    if (v == null) return null;
    final t = v.trim();
    return t.isEmpty ? null : t;
  }

  @override
  Future<void> save(String token) async => _value = token;

  @override
  Future<void> clear() async => _value = null;
}
