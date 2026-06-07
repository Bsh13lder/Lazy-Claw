// Regression tests for the "database load conflict": the app opens the SAME
// encrypted SQLCipher file from TWO isolates (the foreground app AND the
// WorkManager headless sync isolate). Without a busy timeout, a write in one
// isolate while the other holds a lock throws `SQLITE_BUSY` ("database is
// locked") immediately, which bubbled up as a degraded/error screen. The fix:
//
//   * [configureAppDb] sets `busy_timeout` (wait, don't throw) + WAL journal
//     mode (readers don't block the writer) on EVERY connection, AND
//   * [openAppDbWithFallback] retries a TRANSIENT lock instead of degrading to
//     an empty in-memory DB (which would hide the user's healthy on-disk cache).
//
// Verified against a REAL SQLite engine (sqflite_common_ffi) — two genuine
// connections to one on-disk file contend via real OS file locks, so the
// busy_timeout behaviour is exercised end-to-end, not mocked.

import 'dart:async';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

Future<int> _busyTimeout(Database db) async {
  final rows = await db.rawQuery('PRAGMA busy_timeout');
  return rows.first.values.first as int;
}

Future<String> _journalMode(Database db) async {
  final rows = await db.rawQuery('PRAGMA journal_mode');
  return (rows.first.values.first as String).toLowerCase();
}

void main() {
  sqfliteFfiInit();
  databaseFactory = databaseFactoryFfi;

  late Directory tmp;
  setUp(() => tmp = Directory.systemTemp.createTempSync('lazyclaw_dbtest'));
  tearDown(() {
    if (tmp.existsSync()) tmp.deleteSync(recursive: true);
  });

  String dbFile(String name) => '${tmp.path}/$name.db';

  group('configureAppDb PRAGMAs', () {
    test('applies a 5s busy_timeout', () async {
      final db = await databaseFactory.openDatabase(
        inMemoryDatabasePath,
        options: OpenDatabaseOptions(onConfigure: configureAppDb),
      );
      addTearDown(db.close);
      expect(await _busyTimeout(db), kBusyTimeoutMs);
      expect(kBusyTimeoutMs, 5000);
    });

    test('switches the on-disk journal to WAL', () async {
      // WAL requires a real file (an in-memory DB always reports `memory`).
      final db = await databaseFactory.openDatabase(
        dbFile('wal'),
        options: OpenDatabaseOptions(onConfigure: configureAppDb),
      );
      addTearDown(db.close);
      expect(await _journalMode(db), 'wal');
    });
  });

  group('isDatabaseLockedError', () {
    test('matches the SQLite lock/busy tokens (case-insensitive)', () {
      expect(isDatabaseLockedError(
          Exception('database is locked (code 5 SQLITE_BUSY)')), isTrue);
      expect(isDatabaseLockedError(Exception('SQLITE_BUSY')), isTrue);
      expect(isDatabaseLockedError(Exception('database table is locked')),
          isTrue);
      expect(isDatabaseLockedError(Exception('Sqlite_Locked')), isTrue);
    });

    test('does NOT match unrelated failures (corruption / locked keychain)',
        () {
      expect(isDatabaseLockedError(null), isFalse);
      expect(isDatabaseLockedError(StateError('keychain locked')), isFalse);
      expect(isDatabaseLockedError(Exception('disk image is malformed')),
          isFalse);
      expect(isDatabaseLockedError(Exception('out of memory')), isFalse);
    });
  });

  group('openAppDbWithFallback lock handling', () {
    test('retries a TRANSIENT lock far longer before degrading', () async {
      var calls = 0;
      final result = await openAppDbWithFallback(
        retries: 2,
        openImpl: () async {
          calls++;
          throw Exception('database is locked (code 5 SQLITE_BUSY)');
        },
        openInMemory: () async {
          final db = await databaseFactory.openDatabase(inMemoryDatabasePath);
          await createAppDbSchema(db);
          return db;
        },
      );
      // retries(2) + _kLockExtraRetries(4) + 1 initial = 7 attempts.
      expect(calls, 7);
      expect(result.health.isDegraded, isTrue);
    });

    test('a hard (non-lock) failure still degrades after `retries`', () async {
      var calls = 0;
      final result = await openAppDbWithFallback(
        retries: 2,
        openImpl: () async {
          calls++;
          throw StateError('disk image is malformed');
        },
        openInMemory: () async {
          final db = await databaseFactory.openDatabase(inMemoryDatabasePath);
          await createAppDbSchema(db);
          return db;
        },
      );
      expect(calls, 3); // 1 + retries(2)
      expect(result.health.isDegraded, isTrue);
    });
  });

  // Two genuine connections to one on-disk file contend via real OS file locks.
  //
  // NOTE on the harness: `sqflite_common_ffi` serializes EVERY connection onto a
  // single background worker isolate, so a blocking busy-wait on one connection
  // starves the other's commit — the "second writer eventually succeeds" path is
  // structurally unreachable here (it works on a real device, where the native
  // SQLCipher connections run on independent threads). What we CAN prove — and
  // what actually matters — is that our `busy_timeout` PRAGMA governs the wait:
  // a high timeout makes the contended writer BLOCK before failing, a ~zero
  // timeout makes it fail instantly. That is the exact mechanism that turns an
  // instant "database is locked" crash into a wait-and-retry on device.
  group('busy_timeout governs lock-contention behaviour', () {
    /// Open a fresh connection on [file] with a custom busy_timeout (and WAL),
    /// then run [body] while connection `a` holds an open write transaction.
    /// Returns how long [body]'s contended write took + whether it threw a lock
    /// error. The held lock is released only AFTER [body] settles.
    Future<({int elapsedMs, bool threwLock})> contend({
      required String file,
      required int writerTimeoutMs,
    }) async {
      final a = await databaseFactory.openDatabase(
        dbFile(file),
        options: OpenDatabaseOptions(
          singleInstance: false,
          onConfigure: configureAppDb,
          onCreate: (db, _) => createAppDbSchema(db),
          version: kAppDbVersion,
        ),
      );
      final w = await databaseFactory.openDatabase(
        dbFile(file),
        options: OpenDatabaseOptions(
          singleInstance: false,
          onConfigure: (db) async {
            await db.rawQuery('PRAGMA busy_timeout = $writerTimeoutMs');
            await db.rawQuery('PRAGMA journal_mode = WAL');
          },
        ),
      );
      addTearDown(a.close);
      addTearDown(w.close);

      final lockHeld = Completer<void>();
      final release = Completer<void>();
      final txn = a.transaction((t) async {
        await t.insert('task_cache', {'id': 'a', 'title': 'A'});
        lockHeld.complete();
        await release.future; // hold the write lock until the contender settles
      });

      await lockHeld.future;
      final sw = Stopwatch()..start();
      var threwLock = false;
      try {
        await w.insert('task_cache', {'id': 'w', 'title': 'W'});
      } catch (e) {
        threwLock = isDatabaseLockedError(e);
      }
      sw.stop();
      release.complete();
      await txn;
      return (elapsedMs: sw.elapsedMilliseconds, threwLock: threwLock);
    }

    test('a real busy_timeout makes a contended writer WAIT before failing',
        () async {
      // 600ms keeps the test fast; the same handler governs the production 5s.
      final r = await contend(file: 'wait', writerTimeoutMs: 600);
      expect(r.threwLock, isTrue,
          reason: 'lock stayed held, so it must surface as a lock error');
      expect(r.elapsedMs, greaterThanOrEqualTo(400),
          reason: 'busy_timeout should make it block, not fail instantly');
    });

    test('a ~zero busy_timeout fails on contention almost immediately',
        () async {
      final r = await contend(file: 'instant', writerTimeoutMs: 1);
      expect(r.threwLock, isTrue);
      expect(r.elapsedMs, lessThan(300),
          reason: 'with no timeout SQLite returns SQLITE_BUSY right away');
    });
  });

  group('migration across a real open cycle (v3 -> v4)', () {
    test('onConfigure + onUpgrade coexist; color lands once and data survives',
        () async {
      final path = dbFile('migrate');

      // Open #1 at v3 with a color-less project_cache (the pre-v4 shape) and a row.
      final v3 = await databaseFactory.openDatabase(
        path,
        options: OpenDatabaseOptions(
          version: 3,
          singleInstance: false,
          onConfigure: configureAppDb,
          onCreate: (db, _) async {
            await db.execute('''
              CREATE TABLE project_cache (
                id TEXT PRIMARY KEY,
                name TEXT,
                budget REAL,
                updated_at TEXT,
                dirty INTEGER NOT NULL DEFAULT 0,
                deleted INTEGER NOT NULL DEFAULT 0
              )
            ''');
            await db.insert('project_cache', {'id': 'p1', 'name': 'Legacy'});
          },
        ),
      );
      await v3.close();

      Future<List<Map<String, Object?>>> cols(Database db) =>
          db.rawQuery("PRAGMA table_info('project_cache')");
      bool hasColor(List<Map<String, Object?>> c) =>
          c.any((m) => m['name'] == 'color');

      // Open #2 at v4 -> onUpgrade(3,4) runs the guarded ALTER.
      final v4 = await databaseFactory.openDatabase(
        path,
        options: OpenDatabaseOptions(
          version: kAppDbVersion,
          singleInstance: false,
          onConfigure: configureAppDb,
          onCreate: (db, _) => createAppDbSchema(db),
          onUpgrade: migrateAppDb,
        ),
      );
      expect(hasColor(await cols(v4)), isTrue);
      // Existing data preserved; new column defaults to null.
      final row =
          (await v4.query('project_cache', where: 'id = ?', whereArgs: ['p1']))
              .single;
      expect(row['name'], 'Legacy');
      expect(row['color'], isNull);
      await v4.close();

      // Open #3 again at v4 -> no migration runs, no duplicate-column throw.
      final again = await databaseFactory.openDatabase(
        path,
        options: OpenDatabaseOptions(
          version: kAppDbVersion,
          singleInstance: false,
          onConfigure: configureAppDb,
          onCreate: (db, _) => createAppDbSchema(db),
          onUpgrade: migrateAppDb,
        ),
      );
      final after = await cols(again);
      expect(hasColor(after), isTrue);
      // exactly ONE color column.
      expect(after.where((m) => m['name'] == 'color').length, 1);
      await again.close();
    });
  });
}
