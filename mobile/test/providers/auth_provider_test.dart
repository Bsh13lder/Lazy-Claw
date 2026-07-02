import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/core/auth/auth_cache.dart';
import 'package:lazyclaw_mobile/core/constants/app_constants.dart';
import 'package:lazyclaw_mobile/providers/auth_provider.dart';
import 'package:lazyclaw_mobile/repositories/auth_repository.dart';
import 'package:lazyclaw_mobile/models/user.dart';

const _base = 'http://192.168.1.10:18789';
const _user = User(id: 'u1', username: 'sam', displayName: 'Sam', role: 'user');

class _OkTransport implements AuthTransport {
  int calls = 0;
  @override
  Future<Map<String, dynamic>> postJson(
      String path, Map<String, dynamic> body) async {
    calls++;
    return {'id': 'u1', 'username': 'sam', 'role': 'user'};
  }
}

/// Throws [e] on every call — mirrors the production interceptor, which wraps
/// EVERY failure as `DioException(error: ApiError(statusCode, message))`.
class _ThrowingTransport implements AuthTransport {
  final Object e;
  int calls = 0;
  _ThrowingTransport(this.e);
  @override
  Future<Map<String, dynamic>> postJson(
      String path, Map<String, dynamic> body) async {
    calls++;
    throw e;
  }
}

/// Never completes — drives the meTimeout path.
class _HangingTransport implements AuthTransport {
  int calls = 0;
  final Completer<Map<String, dynamic>> _completer = Completer();
  @override
  Future<Map<String, dynamic>> postJson(
      String path, Map<String, dynamic> body) {
    calls++;
    return _completer.future;
  }
}

DioException _wrapped(int status, String message) => DioException(
      requestOptions: RequestOptions(path: '/api/auth/me'),
      type: status == 0
          ? DioExceptionType.connectionError
          : DioExceptionType.badResponse,
      error: ApiError(status, message),
    );

AuthNotifier _notifier(AuthTransport t,
    {AuthUserCache? cache, Duration? meTimeout}) {
  return AuthNotifier(
    AuthRepository(t),
    cache: cache ?? InMemoryAuthUserCache(),
    meTimeout: meTimeout ?? const Duration(seconds: 5),
    baseUrl: () => _base,
  );
}

Future<AuthUserCache> _seededCache() async {
  final cache = InMemoryAuthUserCache();
  await cache.write(_user, baseUrl: _base);
  return cache;
}

void main() {
  test('login moves unauthenticated -> authenticated', () async {
    final notifier = _notifier(_OkTransport());
    expect(notifier.state.status, AuthStatus.unauthenticated);
    final err = await notifier.login('sam', 'pw12345678');
    expect(err, isNull);
    expect(notifier.state.status, AuthStatus.authenticated);
    expect(notifier.state.user, isA<User>());
  });

  test('handle401 forces logout', () {
    final notifier = _notifier(_OkTransport());
    notifier.state = AuthState.authenticated(_user);
    notifier.handle401();
    expect(notifier.state.status, AuthStatus.unauthenticated);
  });

  test('login surfaces the ApiError message from a wrapped DioException',
      () async {
    final notifier =
        _notifier(_ThrowingTransport(_wrapped(401, 'Bad credentials')));
    final err = await notifier.login('sam', 'nope');
    expect(err, 'Bad credentials');
    expect(notifier.state.status, AuthStatus.unauthenticated);
  });

  test('login success writes the user to the cache', () async {
    final cache = InMemoryAuthUserCache();
    final notifier = _notifier(_OkTransport(), cache: cache);
    await notifier.login('sam', 'pw12345678');
    final cached = await cache.read(baseUrl: _base);
    expect(cached, isNotNull);
    expect(cached!.username, 'sam');
  });

  // ── checkSession state machine ─────────────────────────────────────────────

  test('checkSession success -> authenticated + cache written', () async {
    final cache = InMemoryAuthUserCache();
    final notifier = _notifier(_OkTransport(), cache: cache);
    await notifier.checkSession();
    expect(notifier.state.status, AuthStatus.authenticated);
    expect(notifier.state.user?.username, 'sam');
    final cached = await cache.read(baseUrl: _base);
    expect(cached?.username, 'sam');
  });

  test('connectionError + cache hit -> authenticatedOffline with cached user',
      () async {
    final cache = await _seededCache();
    final notifier = _notifier(
      _ThrowingTransport(_wrapped(0, 'Connection failed')),
      cache: cache,
    );
    await notifier.checkSession();
    expect(notifier.state.status, AuthStatus.authenticatedOffline);
    expect(notifier.state.user?.username, 'sam');
    expect(notifier.state.user?.displayName, 'Sam');
    expect(notifier.state.isAuthed, isTrue);
  });

  test('connectionError + empty cache -> unauthenticated', () async {
    final notifier = _notifier(
      _ThrowingTransport(_wrapped(0, 'Connection failed')),
      cache: InMemoryAuthUserCache(),
    );
    await notifier.checkSession();
    expect(notifier.state.status, AuthStatus.unauthenticated);
    expect(notifier.state.isAuthed, isFalse);
  });

  test('ApiError(401) inside DioException -> unauthenticated + cache cleared',
      () async {
    final cache = await _seededCache();
    final notifier = _notifier(
      _ThrowingTransport(_wrapped(401, 'Session expired')),
      cache: cache,
    );
    await notifier.checkSession();
    expect(notifier.state.status, AuthStatus.unauthenticated);
    expect(await cache.read(baseUrl: _base), isNull);
  });

  test('raw ApiError(500) + cache hit -> authenticatedOffline', () async {
    final cache = await _seededCache();
    final notifier = _notifier(
      _ThrowingTransport(ApiError(500, 'Internal error')),
      cache: cache,
    );
    await notifier.checkSession();
    expect(notifier.state.status, AuthStatus.authenticatedOffline);
    expect(notifier.state.user?.username, 'sam');
  });

  test('hanging transport + 10ms meTimeout -> authenticatedOffline', () async {
    final cache = await _seededCache();
    final notifier = _notifier(
      _HangingTransport(),
      cache: cache,
      meTimeout: const Duration(milliseconds: 10),
    );
    await notifier.checkSession();
    expect(notifier.state.status, AuthStatus.authenticatedOffline);
  });

  test(
      'silent upgrade: offline -> authenticated without ever passing through '
      'loading or unauthenticated', () async {
    final cache = await _seededCache();
    final notifier = _notifier(_OkTransport(), cache: cache);
    notifier.state = AuthState.authenticatedOffline(_user);

    final seen = <AuthStatus>[];
    notifier.addListener((s) => seen.add(s.status), fireImmediately: false);

    await notifier.checkSession();
    expect(notifier.state.status, AuthStatus.authenticated);
    expect(seen, isNot(contains(AuthStatus.loading)));
    expect(seen, isNot(contains(AuthStatus.unauthenticated)));
  });

  test('upgrade hitting a 401 -> unauthenticated + cache cleared', () async {
    final cache = await _seededCache();
    final notifier = _notifier(
      _ThrowingTransport(_wrapped(401, 'Session expired')),
      cache: cache,
    );
    notifier.state = AuthState.authenticatedOffline(_user);
    await notifier.checkSession();
    expect(notifier.state.status, AuthStatus.unauthenticated);
    expect(await cache.read(baseUrl: _base), isNull);
  });

  test('checkSession is single-flight (in-flight guard)', () async {
    final transport = _HangingTransport();
    final notifier = _notifier(
      transport,
      cache: await _seededCache(),
      meTimeout: const Duration(milliseconds: 50),
    );
    final first = notifier.checkSession();
    final second = notifier.checkSession(); // must not hit the transport again
    await Future.wait([first, second]);
    expect(transport.calls, 1);
  });

  test('logout clears the cache', () async {
    final cache = await _seededCache();
    final notifier = _notifier(_OkTransport(), cache: cache);
    notifier.state = AuthState.authenticated(_user);
    await notifier.logout();
    expect(notifier.state.status, AuthStatus.unauthenticated);
    expect(await cache.read(baseUrl: _base), isNull);
  });

  test('handle401 clears the cache', () async {
    final cache = await _seededCache();
    final notifier = _notifier(_OkTransport(), cache: cache);
    notifier.state = AuthState.authenticated(_user);
    notifier.handle401();
    expect(notifier.state.status, AuthStatus.unauthenticated);
    // clear() is fired unawaited — give the microtask queue a beat.
    await Future<void>.delayed(Duration.zero);
    expect(await cache.read(baseUrl: _base), isNull);
  });

  test('cache read returns null on base_url mismatch -> unauthenticated',
      () async {
    final cache = InMemoryAuthUserCache();
    await cache.write(_user, baseUrl: 'http://old-server:18789');
    final notifier = _notifier(
      _ThrowingTransport(_wrapped(0, 'Connection failed')),
      cache: cache,
    );
    await notifier.checkSession();
    expect(notifier.state.status, AuthStatus.unauthenticated);
  });

  test(
      'host-flip across server aliases keeps the user signed in offline '
      '(the "asks for login all the time" regression)', () async {
    // Logged in under the .local alias, cache written there...
    final cache = InMemoryAuthUserCache();
    await cache.write(_user, baseUrl: kLanFallbackBaseUrl);
    // ...the runtime gateway flips to the LAN-IP alias, which is unreachable at
    // this instant (connectionError). Same box, different name → the offline
    // identity must survive instead of dumping the user on the login screen.
    final notifier = AuthNotifier(
      AuthRepository(_ThrowingTransport(_wrapped(0, 'Connection failed'))),
      cache: cache,
      meTimeout: const Duration(seconds: 5),
      baseUrl: () => kLanFallbackIpBaseUrl,
    );
    await notifier.checkSession();
    expect(notifier.state.status, AuthStatus.authenticatedOffline);
    expect(notifier.state.user?.username, 'sam');
  });
}
