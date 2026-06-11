// Tests for connectErrorMessage — the chat connect-banner error mapper.
//
// The banner used to render `e.toString()` raw, so on-device failures like
// `DatabaseException(database_closed 1)` leaked straight to the user. The
// mapper must always return a short human message and NEVER echo raw
// exception text.

import 'dart:async';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/chat/connect_error.dart';
import 'package:sqflite_sqlcipher/sqflite.dart' show DatabaseException;

/// DatabaseException is abstract in sqflite_common; the concrete
/// SqfliteDatabaseException is not exported. A minimal subclass reproduces the
/// exact on-device shape (`DatabaseException(database_closed 1)`).
class _TestDatabaseException extends DatabaseException {
  _TestDatabaseException(super.message);

  @override
  int? getResultCode() => 1;

  @override
  Object? get result => null;
}

void main() {
  group('connectErrorMessage', () {
    test('maps DatabaseException to the local-storage message', () {
      final e = _TestDatabaseException('database_closed 1');
      expect(connectErrorMessage(e), kConnectErrorStorage);
    });

    test('never echoes raw DatabaseException text to the user', () {
      final e = _TestDatabaseException('database_closed 1');
      final msg = connectErrorMessage(e);
      expect(msg.contains('database_closed'), isFalse);
      expect(msg.contains('DatabaseException'), isFalse);
    });

    test('maps a SocketException to the connectivity message', () {
      expect(
        connectErrorMessage(const SocketException('Connection refused')),
        kConnectErrorGeneric,
      );
    });

    test('maps a TimeoutException to the connectivity message', () {
      expect(
        connectErrorMessage(TimeoutException('WS dial timed out')),
        kConnectErrorGeneric,
      );
    });

    test('maps an arbitrary error to the connectivity message (fail closed)',
        () {
      expect(
        connectErrorMessage(StateError('totally unexpected')),
        kConnectErrorGeneric,
      );
      expect(connectErrorMessage('a string error'), kConnectErrorGeneric);
    });

    test('messages are short and human (no stack-trace-ish content)', () {
      for (final msg in [kConnectErrorStorage, kConnectErrorGeneric]) {
        expect(msg.length, lessThan(60));
        expect(msg.contains('Exception'), isFalse);
      }
    });
  });
}
