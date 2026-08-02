// v11 → v12 migration: adds `task_cache.comments` (comment-thread JSON) and
// the `ui_prefs` KV table (persisted collapse/hide-completed UI state).
// Verified against a REAL SQLite engine, from genuinely v11-shaped tables
// (created WITHOUT the new column/table) so the ALTER/CREATE branch actually
// runs — createAppDbSchema would already carry them and mask a broken
// migration.

import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

void main() {
  sqfliteFfiInit();
  databaseFactory = databaseFactoryFfi;

  late Directory tmp;
  var dbCounter = 0;
  setUp(() => tmp = Directory.systemTemp.createTempSync('lazyclaw_mig12'));
  tearDown(() {
    if (tmp.existsSync()) tmp.deleteSync(recursive: true);
  });

  /// A DB whose task_cache has the PRE-v12 shape (no `comments` column) and
  /// no `ui_prefs` table at all, with one data row so the migration's data
  /// preservation is provable.
  Future<Database> openV11Shaped() async {
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
    await db.insert('task_cache', {'id': 't1', 'title': 'Existing task'});
    return db;
  }

  Future<Set<Object?>> cols(Database db, String table) async {
    final rows = await db.rawQuery("PRAGMA table_info('$table')");
    return rows.map((c) => c['name']).toSet();
  }

  Future<bool> tableExists(Database db, String table) async {
    final rows = await db.rawQuery(
      "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
      [table],
    );
    return rows.isNotEmpty;
  }

  test('kAppDbVersion is at least 12 (comments + ui_prefs ship)', () {
    expect(kAppDbVersion, greaterThanOrEqualTo(12));
  });

  test('the fresh-install schema carries task_cache.comments + ui_prefs',
      () async {
    final db = await databaseFactory.openDatabase(
      '${tmp.path}/fresh.db',
      options: OpenDatabaseOptions(singleInstance: false),
    );
    await createAppDbSchema(db);
    expect(await cols(db, 'task_cache'), contains('comments'));
    expect(await tableExists(db, 'ui_prefs'), isTrue);
    await db.close();
  });

  test('v11→v12 adds the comments column + ui_prefs table and preserves rows',
      () async {
    final db = await openV11Shaped();

    await migrateAppDb(db, 11, 12);

    expect(await cols(db, 'task_cache'), contains('comments'));
    expect(await tableExists(db, 'ui_prefs'), isTrue);

    // Existing data survives, new column reads back null.
    final task = (await db.query('task_cache')).single;
    expect(task['title'], 'Existing task');
    expect(task['comments'], isNull);
    await db.close();
  });

  test('re-running the v12 branch is idempotent (no duplicate-column throw)',
      () async {
    final db = await openV11Shaped();
    await migrateAppDb(db, 11, 12);
    await migrateAppDb(db, 11, 12); // must not throw
    expect(await cols(db, 'task_cache'), contains('comments'));
    expect(await tableExists(db, 'ui_prefs'), isTrue);
    await db.close();
  });
}
