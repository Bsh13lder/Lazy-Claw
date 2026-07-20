// v9 → v10 migration: the budget ledger (`budget_entry_cache`, added in v9)
// shares ONE 'budgets' sync cursor with projects + expenses. A user who ran the
// app before v9 already had that cursor advanced (by prior project/expense
// pulls) PAST their existing top-ups, so the delta pull returned zero
// budget_entries and the per-project ledger stayed EMPTY + the project budget
// STALE (2026-07-20 incident: 12 server top-ups never reached the phone).
//
// The v10 migration clears the shared 'budgets' cursor exactly once, so the next
// sync does a full (since=null) re-pull that backfills every server top-up and
// the authoritative project budget. Verified against a REAL SQLite engine.

import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

void main() {
  sqfliteFfiInit();
  databaseFactory = databaseFactoryFfi;

  late Directory tmp;
  setUp(() => tmp = Directory.systemTemp.createTempSync('lazyclaw_mig'));
  tearDown(() {
    if (tmp.existsSync()) tmp.deleteSync(recursive: true);
  });

  Future<Database> openWithSchema() async {
    final db = await databaseFactory.openDatabase(
      '${tmp.path}/mig.db',
      options: OpenDatabaseOptions(singleInstance: false),
    );
    await createAppDbSchema(db);
    return db;
  }

  test('kAppDbVersion is at least 10 (the budgets-cursor rewind ships)', () {
    expect(kAppDbVersion, greaterThanOrEqualTo(10));
  });

  test('v9→v10 clears the shared budgets cursor so a full re-pull backfills '
      'the stranded top-ups', () async {
    final db = await openWithSchema();
    // Simulate an upgraded user: the shared 'budgets' cursor sits at the moment
    // the phone went offline — AFTER every existing top-up.
    await db.insert('sync_state', {
      'entity': 'budgets',
      'cursor': '2026-07-16T09:00:00.000Z',
    });

    await migrateAppDb(db, 9, 10);

    final rows = await db.query('sync_state',
        where: 'entity = ?', whereArgs: ['budgets']);
    expect(rows, isEmpty,
        reason: 'a null cursor forces fetchChanges(since=null) = full re-pull');
    await db.close();
  });

  test('v9→v10 leaves OTHER cursors untouched (only budgets is rewound)',
      () async {
    final db = await openWithSchema();
    await db.insert('sync_state', {'entity': 'budgets', 'cursor': 'x'});
    await db.insert('sync_state', {'entity': 'tasks', 'cursor': 'keep-me'});
    await db.insert('sync_state', {'entity': 'notes', 'cursor': 'keep-me-too'});

    await migrateAppDb(db, 9, 10);

    final tasks = await db.query('sync_state',
        where: 'entity = ?', whereArgs: ['tasks']);
    final notes = await db.query('sync_state',
        where: 'entity = ?', whereArgs: ['notes']);
    expect(tasks.single['cursor'], 'keep-me');
    expect(notes.single['cursor'], 'keep-me-too');
    await db.close();
  });

  test('a fresh install (onCreate, no upgrade) has no budgets cursor to clear '
      'and the migration is a no-op', () async {
    final db = await openWithSchema();
    // No sync_state rows at all — the rewind must not throw.
    await migrateAppDb(db, 9, 10);
    final rows = await db.query('sync_state');
    expect(rows, isEmpty);
    await db.close();
  });

  test('running the full migration chain from an old version also rewinds',
      () async {
    // A user upgrading across several versions at once (e.g. v6→v10) must still
    // get the budgets cursor cleared.
    final db = await openWithSchema();
    await db.insert('sync_state', {'entity': 'budgets', 'cursor': 'stale'});
    await migrateAppDb(db, 6, 10);
    final rows = await db.query('sync_state',
        where: 'entity = ?', whereArgs: ['budgets']);
    expect(rows, isEmpty);
    await db.close();
  });
}
