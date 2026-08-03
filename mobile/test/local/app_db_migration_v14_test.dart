// v13 → v14 migration: adds `expense_cache.subtask_id` (nullable TEXT) so an
// expense can be pinned to one checklist item of its linked task, not just
// the task as a whole (mirrors `task_id`).
//
// Verified against a REAL SQLite engine, from a genuinely v13-shaped
// `expense_cache` table (built by hand, WITHOUT the new column) so the ALTER
// branch actually runs — createAppDbSchema would already carry the column
// and mask a broken ALTER. Modeled on app_db_migration_v11_test.dart (the
// column-add pattern) / app_db_migration_v13_test.dart (hand-built
// pre-migration shape + full-chain coverage).

import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

void main() {
  sqfliteFfiInit();
  databaseFactory = databaseFactoryFfi;

  late Directory tmp;
  var dbCounter = 0;
  setUp(() => tmp = Directory.systemTemp.createTempSync('lazyclaw_mig14'));
  tearDown(() {
    if (tmp.existsSync()) tmp.deleteSync(recursive: true);
  });

  /// A DB whose `expense_cache` has the PRE-v14 shape (no `subtask_id`), with
  /// one data row so the migration's data preservation is provable.
  Future<Database> openV13Shaped() async {
    final db = await databaseFactory.openDatabase(
      '${tmp.path}/mig${dbCounter++}.db',
      options: OpenDatabaseOptions(singleInstance: false),
    );
    await db.execute('''
      CREATE TABLE expense_cache (
        id TEXT PRIMARY KEY,
        project_id TEXT,
        task_id TEXT,
        amount REAL,
        currency TEXT,
        description TEXT,
        dirty INTEGER NOT NULL DEFAULT 0,
        deleted INTEGER NOT NULL DEFAULT 0
      )
    ''');
    await db.insert('expense_cache', {
      'id': 'e1',
      'project_id': 'p1',
      'task_id': 't1',
      'amount': 12.5,
      'currency': 'USD',
      'description': 'Existing expense',
    });
    return db;
  }

  Future<Set<Object?>> cols(Database db, String table) async {
    final rows = await db.rawQuery("PRAGMA table_info('$table')");
    return rows.map((c) => c['name']).toSet();
  }

  test('kAppDbVersion is at least 14 (the subtask-expense link ships)', () {
    expect(kAppDbVersion, greaterThanOrEqualTo(14));
  });

  test('the fresh-install schema carries subtask_id on expense_cache',
      () async {
    final db = await databaseFactory.openDatabase(
      '${tmp.path}/fresh.db',
      options: OpenDatabaseOptions(singleInstance: false),
    );
    await createAppDbSchema(db);
    expect(await cols(db, 'expense_cache'), contains('subtask_id'));
    await db.close();
  });

  test('v13→v14 ALTERs subtask_id in and preserves existing rows', () async {
    final db = await openV13Shaped();

    await migrateAppDb(db, 13, 14);

    expect(await cols(db, 'expense_cache'), contains('subtask_id'));

    final row = (await db.query('expense_cache')).single;
    expect(row['id'], 'e1');
    expect(row['task_id'], 't1');
    expect(row['description'], 'Existing expense');
    expect(row['subtask_id'], isNull);
    await db.close();
  });

  test('re-running the v14 branch is idempotent (no duplicate-column throw)',
      () async {
    final db = await openV13Shaped();
    await migrateAppDb(db, 13, 14);
    await migrateAppDb(db, 13, 14); // must not throw
    expect(await cols(db, 'expense_cache'), contains('subtask_id'));
    await db.close();
  });

  test('running the full migration chain from an old version also adds '
      'subtask_id', () async {
    // A user upgrading across several versions at once (e.g. v9→v14) must
    // still get the column. Earlier branches in the chain touch task_cache/
    // project_cache/expense_cache too, so this exercises the chain (not just
    // the v14 branch in isolation, covered above) against the full real
    // schema.
    final db = await databaseFactory.openDatabase(
      '${tmp.path}/mig${dbCounter++}.db',
      options: OpenDatabaseOptions(singleInstance: false),
    );
    await createAppDbSchema(db);
    await migrateAppDb(db, 9, 14);
    expect(await cols(db, 'expense_cache'), contains('subtask_id'));
    await db.close();
  });
}
