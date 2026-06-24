import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api/api_client.dart';
import '../core/api/api_exceptions.dart';
import '../core/auth/auth_cache.dart';
import '../core/home_widget_tasks.dart';
import '../models/user.dart';
import '../repositories/auth_repository.dart';

/// [authenticatedOffline] = the server could not be reached but a previously
/// confirmed identity exists in the local [AuthUserCache]: the user enters the
/// app and the offline-first surfaces (Tasks/Notes/Budgets) keep working.
/// Online-only surfaces should treat it as "signed in, but degraded".
enum AuthStatus { loading, authenticated, unauthenticated, authenticatedOffline }

class AuthState {
  final AuthStatus status;
  final User? user;
  const AuthState(this.status, this.user);
  factory AuthState.loading() => const AuthState(AuthStatus.loading, null);
  factory AuthState.authenticated(User u) =>
      AuthState(AuthStatus.authenticated, u);
  factory AuthState.unauthenticated() =>
      const AuthState(AuthStatus.unauthenticated, null);
  factory AuthState.authenticatedOffline(User u) =>
      AuthState(AuthStatus.authenticatedOffline, u);

  /// Signed in — fully (server-confirmed) OR offline (cache-confirmed).
  bool get isAuthed =>
      status == AuthStatus.authenticated ||
      status == AuthStatus.authenticatedOffline;
}

final baseUrlProvider = StateProvider<String>((ref) => 'http://127.0.0.1:18789');

final apiClientProvider = Provider<ApiClient>((ref) {
  return ApiClient(baseUrl: ref.watch(baseUrlProvider));
});

final authRepositoryProvider = Provider<AuthRepository>((ref) =>
    AuthRepository(DioAuthTransport(ref.watch(apiClientProvider))));

class AuthNotifier extends StateNotifier<AuthState> {
  final AuthRepository _repo;
  final AuthUserCache _cache;
  final String Function() _baseUrl;

  /// Budget for the `/api/auth/me` probe in [checkSession]. A dead LAN host
  /// can hang a TCP connect far longer than a user will wait at a splash
  /// screen — after this, fall back to the offline cache.
  final Duration meTimeout;

  /// In-flight guard: [checkSession] is single-flight (launch + reconnect
  /// upgrade can race).
  bool _checking = false;

  AuthNotifier(
    this._repo, {
    AuthUserCache? cache,
    this.meTimeout = const Duration(seconds: 12),
    String Function()? baseUrl,
  })  : _cache = cache ?? SecureAuthUserCache(),
        // Fail closed: without a real base URL the cache never matches.
        _baseUrl = baseUrl ?? (() => ''),
        super(AuthState.unauthenticated());

  void handle401() {
    state = AuthState.unauthenticated();
    // A 401 means the session is gone for good — drop the offline identity so
    // the next offline launch can't resurrect a revoked login.
    unawaited(_cache.clear());
    // Wipe the plaintext home-screen task snapshot — a 401 means the session is
    // gone; the widget must not keep showing the (now signed-out) user's tasks.
    unawaited(clearTasksWidget());
  }

  Future<String?> login(String username, String password) async {
    try {
      final user = await _repo.login(username, password);
      await _cache.write(user, baseUrl: _baseUrl());
      state = AuthState.authenticated(user);
      return null;
    } catch (e) {
      return _errorMessage(e);
    }
  }

  Future<String?> register(String username, String password,
      {String? displayName, String? inviteToken}) async {
    try {
      await _repo.register(username, password,
          displayName: displayName, inviteToken: inviteToken);
      return await login(username, password);
    } catch (e) {
      return _errorMessage(e);
    }
  }

  /// Revalidate the session against the server.
  ///
  /// State machine:
  ///  * server confirms → `authenticated` (+ refresh the offline cache);
  ///  * server REJECTS (401/403) → `unauthenticated` (+ clear the cache);
  ///  * server UNREACHABLE (timeout / connection error / 5xx) → fall back to
  ///    the cached identity: `authenticatedOffline` on a hit, else
  ///    `unauthenticated`.
  ///
  /// When already signed in (either authed status), this is a SILENT
  /// revalidation: no `loading` flash, and an offline session is only ever
  /// upgraded (→ authenticated) or revoked (→ unauthenticated on a real 401).
  Future<void> checkSession() async {
    if (_checking) return;
    _checking = true;
    try {
      if (!state.isAuthed) state = AuthState.loading();
      try {
        final user = await _repo.me().timeout(meTimeout);
        await _cache.write(user, baseUrl: _baseUrl());
        state = AuthState.authenticated(user);
      } catch (e) {
        if (_isAuthRejection(e)) {
          await _cache.clear();
          state = AuthState.unauthenticated();
        } else {
          // Network failure / timeout / server error — NOT a logout. Enter
          // offline mode if we have a cached identity for THIS server.
          final cached = await _cache.read(baseUrl: _baseUrl());
          state = cached != null
              ? AuthState.authenticatedOffline(cached)
              : AuthState.unauthenticated();
        }
      }
    } finally {
      _checking = false;
    }
  }

  Future<void> logout() async {
    state = AuthState.unauthenticated();
    await _cache.clear();
    // Clear the plaintext task snapshot so the home-screen widget doesn't leak
    // the signed-out user's task titles.
    await clearTasksWidget();
  }
}

/// Unwrap the [ApiError] the interceptor packs into every failure
/// (`DioException(error: ApiError(statusCode, message))`), or a raw [ApiError].
ApiError? _apiErrorOf(Object e) {
  if (e is ApiError) return e;
  if (e is DioException) {
    final inner = e.error;
    if (inner is ApiError) return inner;
  }
  return null;
}

/// HTTP status of a failure, if one is knowable. Network-level failures carry
/// `ApiError(0, ...)` (the interceptor maps a missing response to status 0).
int? _statusOf(Object e) {
  final api = _apiErrorOf(e);
  if (api != null) return api.status;
  if (e is DioException) return e.response?.statusCode;
  return null;
}

/// True only when the SERVER explicitly rejected the session (401/403) —
/// the one case where offline fallback must NOT keep the user signed in.
bool _isAuthRejection(Object e) {
  final status = _statusOf(e);
  return status == 401 || status == 403;
}

/// User-facing message for an auth call failure: prefer the server-provided
/// [ApiError.message] over a raw `DioException: ...` dump.
String _errorMessage(Object e) => _apiErrorOf(e)?.message ?? e.toString();

final authProvider =
    StateNotifierProvider<AuthNotifier, AuthState>((ref) {
  final notifier = AuthNotifier(
    ref.watch(authRepositoryProvider),
    cache: SecureAuthUserCache(),
    baseUrl: () => ref.read(baseUrlProvider),
  );
  // Wire the transport's 401 hook whenever this provider is (re)built.
  // Using ref.read here is safe because apiClientProvider has no dependency
  // on authProvider — it only flows in the other direction.
  ref.read(apiClientProvider).on401 = notifier.handle401;
  return notifier;
});
