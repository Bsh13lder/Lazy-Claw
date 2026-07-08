// Verifies the host-agnostic session behaviour: `session_id` is captured from
// Set-Cookie and re-attached to requests on ANY host, so gateway-host drift
// (192.168.0.12 <-> <mac>.local <-> duckdns) can no longer 401 the write path.
//
// Uses a real Dio with a fake adapter that records the outgoing request and
// returns a Set-Cookie — no network, no platform channels.

import 'dart:async';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/session_cookie_interceptor.dart';
import 'package:lazyclaw_mobile/core/auth/session_token_store.dart';

class _FakeAdapter implements HttpClientAdapter {
  RequestOptions? lastRequest;
  List<String> setCookie;

  _FakeAdapter({this.setCookie = const []});

  @override
  Future<ResponseBody> fetch(RequestOptions options,
      Stream<Uint8List>? requestStream, Future<void>? cancelFuture) async {
    lastRequest = options;
    return ResponseBody.fromString('{}', 200, headers: {
      'content-type': ['application/json'],
      if (setCookie.isNotEmpty) 'set-cookie': setCookie,
    });
  }

  @override
  void close({bool force = false}) {}
}

Dio _dio(SessionCookieInterceptor i, _FakeAdapter a, {String base = 'http://host-a:18789'}) {
  final dio = Dio(BaseOptions(baseUrl: base));
  dio.interceptors.add(i);
  dio.httpClientAdapter = a;
  return dio;
}

String? _cookieHeaderOf(RequestOptions? o) {
  final v = o?.headers['Cookie'] ?? o?.headers['cookie'];
  return v?.toString();
}

void main() {
  group('pure helpers', () {
    test('parseSessionId extracts a value and ignores deletion/empty/absent', () {
      expect(
          SessionCookieInterceptor.parseSessionId(
              'session_id=abc123; Path=/; HttpOnly'),
          'abc123');
      // deletion cookie a logout sends → null (must not clobber a live token)
      expect(
          SessionCookieInterceptor.parseSessionId('session_id=; Max-Age=0'),
          isNull);
      expect(
          SessionCookieInterceptor.parseSessionId('other=x; Path=/'), isNull);
    });

    test('hasSessionCookie detects presence only as a real pair', () {
      expect(SessionCookieInterceptor.hasSessionCookie('session_id=x'), isTrue);
      expect(SessionCookieInterceptor.hasSessionCookie('a=1; session_id=x'),
          isTrue);
      expect(SessionCookieInterceptor.hasSessionCookie('a=1; b=2'), isFalse);
      expect(SessionCookieInterceptor.hasSessionCookie(''), isFalse);
    });

    test('mergeCookie appends without dropping existing pairs', () {
      expect(SessionCookieInterceptor.mergeCookie('', 'tok'), 'session_id=tok');
      expect(SessionCookieInterceptor.mergeCookie('a=1', 'tok'),
          'a=1; session_id=tok');
    });
  });

  group('interceptor behaviour (real Dio, fake adapter)', () {
    test('attaches the stored session_id to a request on ANY host', () async {
      final store = InMemorySessionTokenStore('TOKEN1');
      final adapter = _FakeAdapter();
      final dio = _dio(SessionCookieInterceptor(store), adapter,
          base: 'http://192.168.0.12:18789');

      await dio.get('/api/tasks');

      expect(_cookieHeaderOf(adapter.lastRequest), contains('session_id=TOKEN1'));
    });

    test('does NOT duplicate when the request already carries session_id',
        () async {
      final store = InMemorySessionTokenStore('TOKEN1');
      final adapter = _FakeAdapter();
      final dio = _dio(SessionCookieInterceptor(store), adapter);

      await dio.get('/api/tasks',
          options: Options(headers: {'Cookie': 'session_id=EXISTING'}));

      final header = _cookieHeaderOf(adapter.lastRequest)!;
      expect(header, contains('session_id=EXISTING'));
      expect('session_id='.allMatches(header).length, 1);
    });

    test('with no stored token, attaches nothing', () async {
      final store = InMemorySessionTokenStore();
      final adapter = _FakeAdapter();
      final dio = _dio(SessionCookieInterceptor(store), adapter);

      await dio.get('/api/health');

      final header = _cookieHeaderOf(adapter.lastRequest) ?? '';
      expect(header.contains('session_id='), isFalse);
    });

    test('captures session_id from Set-Cookie and reuses it on the next request',
        () async {
      final store = InMemorySessionTokenStore();
      final adapter = _FakeAdapter(
          setCookie: ['session_id=FRESH; Path=/; HttpOnly']);
      final interceptor = SessionCookieInterceptor(store);
      final dio = _dio(interceptor, adapter);

      // 1st request: no token yet → nothing attached, but response mints one.
      await dio.post('/api/auth/login');
      expect(await store.load(), 'FRESH');

      // 2nd request (login stops setting cookie): the captured token is re-sent.
      adapter.setCookie = const [];
      await dio.get('/api/tasks');
      expect(_cookieHeaderOf(adapter.lastRequest), contains('session_id=FRESH'));
    });
  });
}
