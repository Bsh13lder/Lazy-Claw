// Tests for buildAppDbOpenOptions — the single place that decides HOW the
// encrypted app DB is opened (version, callbacks, SQLCipher password, and the
// singleInstance flag).
//
// WHY singleInstance matters: sqflite keys native DB handles by PATH when
// singleInstance is true (the default). A background isolate (WorkManager
// sync, notification-action handler) that opens the same path therefore gets
// the SAME native handle as the foreground app's appDatabaseProvider — and its
// `db.close()` kills the foreground connection mid-flight, surfacing
// `DatabaseException(database_closed)` on the next foreground query. The
// background open sites must request a DEDICATED connection
// (singleInstance: false) so closing it cannot affect the main isolate.
//
// The final hop (databaseFactory.openDatabase) is a platform method channel
// and is not unit-testable without heavy mocking — these tests pin the options
// plumbing, which is where the bug lived.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';

void main() {
  group('buildAppDbOpenOptions', () {
    test('defaults to a shared (singleInstance: true) connection', () {
      final opts = buildAppDbOpenOptions(password: 'k');
      expect(opts.singleInstance, isTrue);
    });

    test(
        'singleInstance: false requests a dedicated native handle '
        '(background-isolate open sites)', () {
      final opts = buildAppDbOpenOptions(password: 'k', singleInstance: false);
      expect(opts.singleInstance, isFalse);
    });

    test('carries the SQLCipher password through verbatim', () {
      final opts = buildAppDbOpenOptions(password: 'secret-passphrase');
      expect(opts.password, 'secret-passphrase');
    });

    test('pins the schema version to kAppDbVersion', () {
      final opts = buildAppDbOpenOptions(password: 'k');
      expect(opts.version, kAppDbVersion);
    });

    test('wires the production lifecycle callbacks', () {
      final opts = buildAppDbOpenOptions(password: 'k');
      // onConfigure/onUpgrade are the shared top-level functions — identity
      // equality proves the real multi-isolate PRAGMA setup + migrations run
      // for EVERY open variant (foreground and background alike).
      expect(opts.onConfigure, same(configureAppDb));
      expect(opts.onUpgrade, same(migrateAppDb));
      expect(opts.onCreate, isNotNull);
      expect(opts.readOnly, isFalse);
    });

    test('returns a fresh options object per call (no shared mutable state)',
        () {
      final a = buildAppDbOpenOptions(password: 'k');
      final b = buildAppDbOpenOptions(password: 'k');
      expect(identical(a, b), isFalse);
      // A dedicated-connection build must not bleed into the next default
      // build (no shared mutable options object).
      buildAppDbOpenOptions(password: 'k', singleInstance: false);
      expect(buildAppDbOpenOptions(password: 'k').singleInstance, isTrue);
    });
  });
}
