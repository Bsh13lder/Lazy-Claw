import 'package:dio/dio.dart';

import '../auth/session_token_store.dart';

/// Makes the `session_id` session survive gateway-HOST changes.
///
/// The default [PersistCookieJar] only sends a cookie back to the exact host it
/// was set on. A self-hosted server reachable as `192.168.0.12`, `<mac>.local`
/// AND `foo.duckdns.org` therefore loses auth whenever the app switches between
/// those addresses (writes 401, health still 200 → "connected but nothing
/// uploads"). This interceptor:
///
///  * captures `session_id` from every `Set-Cookie` response and persists it
///    host-agnostically (via [SessionTokenStore]);
///  * re-attaches it to the `Cookie` header of every OUTGOING request whose
///    header doesn't already carry it — regardless of host.
///
/// It composes with the existing [CookieManager]: add this AFTER the cookie
/// manager so same-host cookies still flow, and this only fills the gap when the
/// jar has nothing for the current host.
class SessionCookieInterceptor extends Interceptor {
  final SessionTokenStore _store;

  /// In-memory cache so we don't hit secure storage on every request. Seeded on
  /// first use from the store and refreshed from `Set-Cookie`.
  String? _token;
  bool _loaded;

  SessionCookieInterceptor(this._store, {String? initialToken})
      : _token = initialToken,
        _loaded = initialToken != null;

  static const String cookieName = 'session_id';

  Future<void> _ensureLoaded() async {
    if (_loaded) return;
    _token = await _store.load();
    _loaded = true;
  }

  @override
  void onRequest(
      RequestOptions options, RequestInterceptorHandler handler) async {
    await _ensureLoaded();
    final token = _token;
    if (token != null && token.isNotEmpty) {
      final existing = _existingCookieHeader(options.headers);
      if (!hasSessionCookie(existing)) {
        options.headers['Cookie'] = mergeCookie(existing, token);
      }
    }
    handler.next(options);
  }

  @override
  void onResponse(Response response, ResponseInterceptorHandler handler) {
    _captureFromSetCookie(response.headers.map['set-cookie']);
    handler.next(response);
  }

  @override
  void onError(DioException err, ErrorInterceptorHandler handler) {
    // A login that returns non-2xx still shouldn't drop a fresh cookie; capture
    // opportunistically. (Normal logins go through onResponse.)
    _captureFromSetCookie(err.response?.headers.map['set-cookie']);
    handler.next(err);
  }

  void _captureFromSetCookie(List<String>? setCookies) {
    if (setCookies == null) return;
    for (final sc in setCookies) {
      final v = parseSessionId(sc);
      if (v != null) {
        _token = v;
        _loaded = true;
        _store.save(v); // fire-and-forget persist
      }
    }
  }

  static String _existingCookieHeader(Map<String, dynamic> headers) {
    final v = headers['Cookie'] ?? headers['cookie'];
    return v == null ? '' : v.toString();
  }

  /// True when [cookieHeader] already carries a `session_id=` pair.
  static bool hasSessionCookie(String cookieHeader) =>
      RegExp('(^|;\\s*)$cookieName=').hasMatch(cookieHeader);

  /// Append `session_id=<token>` to an existing `Cookie` header value.
  static String mergeCookie(String existing, String token) {
    final pair = '$cookieName=$token';
    if (existing.trim().isEmpty) return pair;
    return '$existing; $pair';
  }

  /// Extract a NON-EMPTY `session_id` value from a single `Set-Cookie` line.
  /// Returns `null` for an absent id or a deletion cookie (empty value, e.g.
  /// the `session_id=; Max-Age=0` a logout sends) so we never clobber a live
  /// token with an empty one.
  static String? parseSessionId(String setCookie) {
    final m = RegExp('(?:^|;\\s*)$cookieName=([^;]*)').firstMatch(setCookie);
    if (m == null) return null;
    final val = m.group(1)?.trim();
    if (val == null || val.isEmpty) return null;
    return val;
  }
}
