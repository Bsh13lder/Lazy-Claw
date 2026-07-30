// v10 → v11 migration: adds `task_cache.recur_until` (recurring-series end)
// and `project_cache.start_date` + `project_cache.due_date` (project time
// frame). Verified against a REAL SQLite engine, from genuinely v10-shaped
// tables (created WITHOUT the new columns) so the ALTER branch actually runs —
// createAppDbSchema would already carry the columns and mask a broken ALTER.

import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

void main() {
  sqfliteFfiInit();
  databaseFactory = databaseFactoryFfi;

  late Directory tmp;
  var dbCounter = 0;
  setUp(() => tmp = Directory.systemTemp.createTempSync('lazyclaw_mig11'));
  tearDown(() {
    if (tmp.existsSync()) tmp.deleteSync(recursive: true);
  });

  /// A DB whose task_cache/project_cache have the PRE-v11 shape (no
  /// recur_until / start_date / due_date), with one data row each so the
  /// migration's data preservation is provable.
  Future<Database> openV10Shaped() async {
    final db = await databaseFactory.openDatabase(
      '${tmp.path}/mig${dbCounter++}.db',
      options: OpenDatabaseOptions(singleInstance: false),
    );
    await db.execute('''
      CREATE TABLE task_cache (
        id TEXT PRIMARY KEY, title TEXT, recurring TEXT,
        dirty INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0
      )
    ''');
    await db.execute('''
      CREATE TABLE project_cache (
        id TEXT PRIMARY KEY, name TEXT, budget REAL,
        dirty INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0
      )
    ''');
    await db.insert('task_cache',
        {'id': 't1', 'title': 'Existing task', 'recurring': '0 9 * * *'});
    await db.insert(
        'project_cache', {'id': 'p1', 'name': 'Existing project', 'budget': 5.0});
    return db;
  }

  Future<Set<Object?>> cols(Database db, String table) async {
    final rows = await db.rawQuery("PRAGMA table_info('$table')");
    return rows.map((c) => c['name']).toSet();
  }

  test('kAppDbVersion is at least 11 (recur_until + project dates ship)', () {
    expect(kAppDbVersion, greaterThanOrEqualTo(11));
  });

  test('the fresh-install schema carries all three new columns', () async {
    final db = await databaseFactory.openDatabase(
      '${tmp.path}/fresh.db',
      options: OpenDatabaseOptions(singleInstance: false),
    );
    await createAppDbSchema(db);
    expect(await cols(db, 'task_cache'), contains('recur_until'));
    final proj = await cols(db, 'project_cache');
    expect(proj, containsAll(['start_date', 'due_date']));
    await db.close();
  });

  test('v10→v11 ALTERs the three columns in and preserves existing rows',
      () async {
    final db = await openV10Shaped();

    await migrateAppDb(db, 10, 11);

    expect(await cols(db, 'task_cache'), contains('recur_until'));
    final proj = await cols(db, 'project_cache');
    expect(proj, containsAll(['start_date', 'due_date']));

    // Existing data survives, new columns read back null.
    final task = (await db.query('task_cache')).single;
    expect(task['title'], 'Existing task');
    expect(task['recur_until'], isNull);
    final project = (await db.query('project_cache')).single;
    expect(project['name'], 'Existing project');
    expect(project['start_date'], isNull);
    expect(project['due_date'], isNull);
    await db.close();
  });

  test('re-running the v11 branch is idempotent (no duplicate-column throw)',
      () async {
    final db = await openV10Shaped();
    await migrateAppDb(db, 10, 11);
    await migrateAppDb(db, 10, 11); // must not throw
    expect(await cols(db, 'task_cache'), contains('recur_until'));
    await db.close();
  });
}
