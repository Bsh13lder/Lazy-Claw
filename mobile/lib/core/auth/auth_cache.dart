import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../../models/user.dart';
import '../constants/app_constants.dart';

/// Secure-storage key for the last successfully authenticated user.
const String kAuthCacheKey = 'auth.last_user';

/// Persists the last user `/api/auth/me` (or login) confirmed, so a
/// previously-logged-in user can still enter the app when the backend is
/// unreachable (offline mode — Tasks/Notes/Budgets keep working from the
/// encrypted local store).
///
/// The payload records the server `base_url` it was issued by: switching to a
/// genuinely DIFFERENT server must NOT let a cached identity from server A
/// unlock offline mode against server B. The ONE exception is the app's own
/// known aliases ([kServerAliases]) — DuckDNS / `.local` / LAN-IP all address
/// the SAME self-hosted box, so an identity cached under one is valid under
/// another. Without this, a runtime gateway host-flip forces a spurious
/// re-login (see [SessionCookieInterceptor] for the same host-drift fix on the
/// session cookie).
abstract class AuthUserCache {
  /// The cached user for [baseUrl], or null when absent / unreadable /
  /// written for a different server. NEVER throws.
  Future<User?> read({required String baseUrl});

  Future<void> write(User u, {required String baseUrl});

  Future<void> clear();
}

/// Encode the cache payload: `{"user": {...}, "base_url": "..."}`.
String encodeAuthCachePayload(User u, String baseUrl) =>
    jsonEncode({'user': u.toJson(), 'base_url': baseUrl});

/// Decode a cache payload back to a [User]. Returns null (never throws) on a
/// missing/empty payload, any parse failure, or a `base_url` mismatch.
User? decodeAuthCachePayload(String? raw, {required String baseUrl}) {
  if (raw == null || raw.isEmpty) return null;
  try {
    final decoded = jsonDecode(raw);
    if (decoded is! Map) return null;
    if (!_sameServer(decoded['base_url'], baseUrl)) return null;
    final userJson = decoded['user'];
    if (userJson is! Map) return null;
    return User.fromJson(Map<String, dynamic>.from(userJson));
  } catch (_) {
    // Corrupt cache = no cache. The caller falls back to unauthenticated.
    return null;
  }
}

/// True when [cached] (a stored `base_url`) and [current] address the SAME
/// server: an exact match, OR both are known aliases of the one self-hosted box
/// ([kServerAliases]). A foreign URL never matches an alias, so offline mode
/// still can't be unlocked against a different server.
bool _sameServer(Object? cached, String current) {
  if (cached is! String) return false;
  if (cached == current) return true;
  return kServerAliases.contains(cached) && kServerAliases.contains(current);
}

/// Production cache backed by [FlutterSecureStorage] (same store the server
/// config + DB key already use). Every method is best-effort: the cache is an
/// availability aid, so a storage hiccup must never break login/logout.
class SecureAuthUserCache implements AuthUserCache {
  final FlutterSecureStorage _storage;

  SecureAuthUserCache({FlutterSecureStorage? storage})
      : _storage = storage ?? const FlutterSecureStorage();

  @override
  Future<User?> read({required String baseUrl}) async {
    try {
      final raw = await _storage.read(key: kAuthCacheKey);
      return decodeAuthCachePayload(raw, baseUrl: baseUrl);
    } catch (_) {
      return null;
    }
  }

  @override
  Future<void> write(User u, {required String baseUrl}) async {
    try {
      await _storage.write(
        key: kAuthCacheKey,
        value: encodeAuthCachePayload(u, baseUrl),
      );
    } catch (_) {
      // Best-effort: a failed cache write only costs the NEXT offline launch.
    }
  }

  @override
  Future<void> clear() async {
    try {
      await _storage.delete(key: kAuthCacheKey);
    } catch (_) {
      // Best-effort: read() is base_url-pinned, so a stale row can't leak
      // across servers even if this delete fails.
    }
  }
}

/// In-memory cache for tests. Stores the ENCODED payload so it honors the
/// exact same base_url-pinning contract as [SecureAuthUserCache].
class InMemoryAuthUserCache implements AuthUserCache {
  String? _raw;

  @override
  Future<User?> read({required String baseUrl}) async =>
      decodeAuthCachePayload(_raw, baseUrl: baseUrl);

  @override
  Future<void> write(User u, {required String baseUrl}) async {
    _raw = encodeAuthCachePayload(u, baseUrl);
  }

  @override
  Future<void> clear() async {
    _raw = null;
  }
}
