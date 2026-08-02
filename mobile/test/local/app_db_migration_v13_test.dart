// v12 → v13 migration: one-time rewind of the 'task' sync cursor
// (`kTaskEntity`). A cursor that ever got ahead of an undelivered row orphans
// it forever — the server filters `updated_at > since`
// (lazyclaw/tasks/store.py:2353-2361) — which is how server-minted recurring
// respawns and agent/web edits go permanently missing from the phone while
// local-only rows keep showing (2026-08-03 incident: 5 dated/recurring open
// tasks never reached task_cache). Deleting the 'task' cursor row makes the
// next `getCursor()` return null → the following sync pulls a FULL snapshot.
// Mirrors the v9→v10 'budgets' rewind (app_db_migration_v10_test.dart) —
// this migration must NOT touch that row.
//
// Verified against a REAL SQLite engine, from a genuinely v12-shaped
// `sync_state` table (built by hand, NOT via `createAppDbSchema`) so the
// DELETE branch actually runs. Modeled on app_db_migration_v12_test.dart.

import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

void main() {
  sqfliteFfiInit();
  databaseFactory = databaseFactoryFfi;

  late Directory tmp;
  var dbCounter = 0;
  setUp(() => tmp = Directory.systemTemp.createTempSync('lazyclaw_mig13'));
  tearDown(() {
    if (tmp.existsSync()) tmp.deleteSync(recursive: true);
  });

  /// A DB with a hand-built v12-shaped `sync_state` table (same shape as the
  /// real schema — `entity TEXT PRIMARY KEY, cursor TEXT` — but built by hand
  /// rather than via `createAppDbSchema` so this test proves the v13 DELETE
  /// branch itself, not just that the shared schema already works).
  Future<Database> openV12Shaped() async {
    final db = await databaseFactory.openDatabase(
      '${tmp.path}/mig${dbCounter++}.db',
      options: OpenDatabaseOptions(singleInstance: false),
    );
    await db.execute('''
      CREATE TABLE sync_state (
        entity TEXT PRIMARY KEY,
        cursor TEXT
      )
    ''');
    return db;
  }

  test('kAppDbVersion is at least 13 (the task-cursor rewind ships)', () {
    expect(kAppDbVersion, greaterThanOrEqualTo(13));
  });

  test('the fresh-install schema still creates sync_state', () async {
    final db = await databaseFactory.openDatabase(
      '${tmp.path}/fresh.db',
      options: OpenDatabaseOptions(singleInstance: false),
    );
    await createAppDbSchema(db);
    final rows = await db.rawQuery(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_state'",
    );
    expect(rows, isNotEmpty);
    await db.close();
  });

  test(
      'v12→v13 deletes the stranded task cursor and leaves the budgets '
      'cursor untouched', () async {
    final db = await openV12Shaped();
    await db.insert('sync_state', {
      'entity': kTaskEntity,
      'cursor': '2026-07-28T00:00:00Z',
    });
    await db.insert('sync_state', {
      'entity': 'budgets',
      'cursor': '2026-07-16T09:00:00.000Z',
    });

    await migrateAppDb(db, 12, 13);

    final taskRows = await db.query('sync_state',
        where: 'entity = ?', whereArgs: [kTaskEntity]);
    expect(taskRows, isEmpty,
        reason:
            'a null cursor forces the next getCursor() → null → a full '
            're-pull that backfills the stranded rows');

    final budgetRows = await db.query('sync_state',
        where: 'entity = ?', whereArgs: ['budgets']);
    expect(budgetRows, hasLength(1));
    expect(budgetRows.single['cursor'], '2026-07-16T09:00:00.000Z');

    await db.close();
  });

  test('a fresh install (no task cursor yet) is a no-op — never throws',
      () async {
    final db = await openV12Shaped();
    await migrateAppDb(db, 12, 13); // must not throw
    final rows = await db.query('sync_state');
    expect(rows, isEmpty);
    await db.close();
  });

  test('re-running the v13 branch is idempotent (no throw on missing row)',
      () async {
    final db = await openV12Shaped();
    await db.insert('sync_state', {'entity': kTaskEntity, 'cursor': 'x'});
    await migrateAppDb(db, 12, 13);
    await migrateAppDb(db, 12, 13); // must not throw the second time
    final rows = await db.query('sync_state',
        where: 'entity = ?', whereArgs: [kTaskEntity]);
    expect(rows, isEmpty);
    await db.close();
  });

  test('running the full migration chain from an old version also rewinds '
      'the task cursor', () async {
    // A user upgrading across several versions at once (e.g. v9→v13) must
    // still get the task cursor cleared. Earlier branches in the chain
    // (v10→v11, v11→v12) touch task_cache/project_cache, so this one needs
    // the FULL real schema rather than the hand-built sync_state-only shape —
    // it's exercising the chain, not proving the v13 branch in isolation
    // (that's the direct migrateAppDb(db, 12, 13) test above).
    final db = await databaseFactory.openDatabase(
      '${tmp.path}/mig${dbCounter++}.db',
      options: OpenDatabaseOptions(singleInstance: false),
    );
    await createAppDbSchema(db);
    await db.insert('sync_state', {'entity': kTaskEntity, 'cursor': 'stale'});
    await migrateAppDb(db, 9, 13);
    final rows = await db.query('sync_state',
        where: 'entity = ?', whereArgs: [kTaskEntity]);
    expect(rows, isEmpty);
    await db.close();
  });
}
