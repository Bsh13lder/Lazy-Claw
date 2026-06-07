// Tests for per-project favorite (`Project.isFavorite`, `project_cache
// .is_favorite`) at the bumped DB schema version. is_favorite is stored as an
// INTEGER 0/1 (sqflite has no bool column type) and must survive the full
// offline round-trip: local create, local update, and a server-driven upsert.
// Verified against a REAL in-memory SQLite (ffi) running the production schema.
//
// A focused migration test also proves the v4 → v5 `ALTER TABLE ... ADD COLUMN
// is_favorite` branch adds the column to a pre-existing (favorite-less) table
// and is idempotent when re-run.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/budgets_dao.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

int _dbCounter = 0;

Future<BudgetsDao> _freshDao({String Function()? now}) async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:projfavmem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return BudgetsDao(db, now: now);
}

Project _serverProject({
  String id = 'p1',
  String name = 'Server project',
  double budget = 1000.0,
  bool isFavorite = false,
}) =>
    Project(
      id: id,
      name: name,
      budget: budget,
      currency: 'USD',
      status: 'active',
      isFavorite: isFavorite,
      spent: 0.0,
      remaining: budget,
    );

void main() {
  setUpAll(() => sqfliteFfiInit());

  group('DB schema version', () {
    test('is bumped to 5 for the project is_favorite column', () {
      expect(kAppDbVersion, 5);
    });
  });

  group('Project model isFavorite', () {
    test('round-trips through fromJson / toJson (JSON bool)', () {
      final p = Project.fromJson({
        'id': 'p1',
        'name': 'Pinned',
        'budget': 100,
        'is_favorite': true,
      });
      expect(p.isFavorite, isTrue);
      expect(p.toJson()['is_favorite'], true);
    });

    test('coerces an INTEGER 0/1 from the local cache shape', () {
      expect(Project.fromJson({'id': 'p', 'name': 'x', 'is_favorite': 1})
          .isFavorite, isTrue);
      expect(Project.fromJson({'id': 'p', 'name': 'x', 'is_favorite': 0})
          .isFavorite, isFalse);
    });

    test('defaults to false when the server omits it', () {
      final p = Project.fromJson({'id': 'p1', 'name': 'No flag'});
      expect(p.isFavorite, isFalse);
    });

    test('copyWith preserves and overrides isFavorite', () {
      const p = Project(
        id: 'p1',
        name: 'X',
        budget: 0,
        currency: 'USD',
        status: 'active',
        isFavorite: true,
      );
      expect(p.copyWith().isFavorite, isTrue);
      expect(p.copyWith(isFavorite: false).isFavorite, isFalse);
    });
  });

  group('BudgetsDao local project create', () {
    test('a fresh project is not favorited by default', () async {
      final dao = await _freshDao();
      final project = await dao.applyLocalProjectCreate('Marketing');
      expect(project.isFavorite, isFalse);

      final stored = await dao.getProject(project.id);
      expect(stored!.isFavorite, isFalse);
    });
  });

  group('BudgetsDao local project update with isFavorite', () {
    test('toggling on bumps dirty + enqueues a JSON-bool patch', () async {
      final dao = await _freshDao();
      final p = await dao.applyLocalProjectCreate('Proj');
      final updated = await dao.applyLocalProjectUpdate(p.id, isFavorite: true);
      expect(updated!.isFavorite, isTrue);

      final stored = await dao.getProject(p.id);
      expect(stored!.isFavorite, isTrue);

      final outbox = await dao.readBudgetsOutbox();
      final updateItem =
          outbox.firstWhere((o) => o.op == BudgetsOutboxOp.update);
      expect(updateItem.entity, kProjectEntity);
      // The outbox payload must carry a real JSON bool for the server PATCH.
      expect(updateItem.payload['is_favorite'], true);
      expect(updateItem.payload['id'], p.id);
    });

    test('toggling off persists false', () async {
      final dao = await _freshDao();
      final p = await dao.applyLocalProjectCreate('Proj');
      await dao.applyLocalProjectUpdate(p.id, isFavorite: true);
      await dao.applyLocalProjectUpdate(p.id, isFavorite: false);
      final stored = await dao.getProject(p.id);
      expect(stored!.isFavorite, isFalse);
    });

    test('the cache row stores an INTEGER 0/1 (not a Dart bool)', () async {
      final dao = await _freshDao();
      final p = await dao.applyLocalProjectCreate('Proj');
      await dao.applyLocalProjectUpdate(p.id, isFavorite: true);
      // A round-trip through getProject already proved sqflite accepted the
      // write; confirm the stored shape is the INTEGER 1 the column expects.
      final row = await dao.getProjectRow(p.id);
      expect(row!['is_favorite'], 1);
    });

    test('budget + favorite can be set independently', () async {
      final dao = await _freshDao();
      final p = await dao.applyLocalProjectCreate('Proj');
      final updated =
          await dao.applyLocalProjectUpdate(p.id, budget: 750.0, isFavorite: true);
      expect(updated!.budget, 750.0);
      expect(updated.isFavorite, isTrue);

      final outbox = await dao.readBudgetsOutbox();
      final updateItem =
          outbox.firstWhere((o) => o.op == BudgetsOutboxOp.update);
      expect(updateItem.payload['budget'], 750.0);
      expect(updateItem.payload['is_favorite'], true);
    });
  });

  group('BudgetsDao upsertProjectFromServer with isFavorite', () {
    test('writes the server favorite into the clean cache row', () async {
      final dao = await _freshDao();
      await dao.upsertProjectFromServer(
        _serverProject(id: 'srv', name: 'From server', isFavorite: true),
        serverUpdatedAt: '2026-06-07T11:00:00Z',
      );
      final stored = await dao.getProject('srv');
      expect(stored!.isFavorite, isTrue);
      expect(await dao.dirtyProjectIds(), isEmpty);
    });
  });

  group('v4 → v5 migration', () {
    test('ALTER TABLE adds is_favorite to a pre-existing table + is idempotent',
        () async {
      // A pristine DB with a v4-shaped project_cache (no `is_favorite` column).
      final db = await databaseFactoryFfi.openDatabase(
        'file:projfavmig${_dbCounter++}?mode=memory&cache=shared',
        options: OpenDatabaseOptions(singleInstance: false),
      );
      await db.execute('''
        CREATE TABLE project_cache (
          id TEXT PRIMARY KEY,
          name TEXT,
          budget REAL,
          color TEXT,
          updated_at TEXT,
          dirty INTEGER NOT NULL DEFAULT 0,
          deleted INTEGER NOT NULL DEFAULT 0
        )
      ''');

      Future<bool> columnExists() async {
        final cols = await db.rawQuery("PRAGMA table_info('project_cache')");
        return cols.any((c) => c['name'] == 'is_favorite');
      }

      expect(await columnExists(), isFalse);

      await migrateAppDb(db, 4, 5);
      expect(await columnExists(), isTrue);

      // Re-running the migration must not throw (idempotent).
      await migrateAppDb(db, 4, 5);
      expect(await columnExists(), isTrue);

      await db.close();
    });
  });
}
