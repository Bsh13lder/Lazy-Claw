import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_client.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';

void main() {
  test('ApiError classifies status codes', () {
    expect(ApiError(401, 'x').isUnauthorized, isTrue);
    expect(ApiError(404, 'x').isNotFound, isTrue);
    expect(ApiError(403, 'x').isForbidden, isTrue);
    expect(ApiError(500, 'x').isUnauthorized, isFalse);
  });

  group('resolveSessionToken (WebSocket session lookup)', () {
    test('host-agnostic store wins over the per-host cookie jar', () {
      // Regression: getSessionCookie only scanned the jar for the CURRENT
      // baseUrl host. After a host change (LAN re-discovery / Funnel /
      // cellular) that jar has no session_id, so the WS auth returned null
      // and the chat screen bounced to login — even though HTTP requests
      // stayed authenticated via the host-agnostic store. The store must win.
      expect(
        ApiClient.resolveSessionToken('STORED', const []),
        'STORED',
      );
      expect(
        ApiClient.resolveSessionToken(
          'STORED',
          [Cookie('session_id', 'JARVALUE')],
        ),
        'STORED',
      );
    });

    test('falls back to the jar session_id when the store is empty', () {
      expect(
        ApiClient.resolveSessionToken(null, [
          Cookie('other', 'x'),
          Cookie('session_id', 'JARVALUE'),
        ]),
        'JARVALUE',
      );
    });

    test('returns null when neither store nor jar has a session', () {
      expect(ApiClient.resolveSessionToken(null, const []), isNull);
      expect(ApiClient.resolveSessionToken('', const []), isNull);
      expect(
        ApiClient.resolveSessionToken(null, [Cookie('session_id', '')]),
        isNull,
      );
    });
  });
}
