// Tests for per-project color (`Project.color`, `project_cache.color`) at the
// bumped DB schema version. Color is a hex string ("#RRGGBB") or null, and must
// survive the full offline round-trip: local create, local update, and a
// server-driven upsert. Verified against a REAL in-memory SQLite (ffi) running
// the production schema — never a hand-rolled fake DB.
//
// A focused migration test also proves the v3 → v4 `ALTER TABLE ... ADD COLUMN
// color` branch adds the column to a pre-existing (color-less) table and is
// idempotent when re-run.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/budgets_dao.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

int _dbCounter = 0;

Future<BudgetsDao> _freshDao({String Function()? now}) async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:projcolormem${_dbCounter++}?mode=memory&cache=shared',
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
  String? color,
}) =>
    Project(
      id: id,
      name: name,
      budget: budget,
      currency: 'USD',
      status: 'active',
      color: color,
      spent: 0.0,
      remaining: budget,
    );

void main() {
  setUpAll(() => sqfliteFfiInit());

  group('DB schema version', () {
    test('is bumped to 4 for the project color column', () {
      expect(kAppDbVersion, 4);
    });
  });

  group('Project model color', () {
    test('round-trips through fromJson / toJson', () {
      final p = Project.fromJson({
        'id': 'p1',
        'name': 'Has color',
        'budget': 100,
        'color': '#FF8A3D',
      });
      expect(p.color, '#FF8A3D');
      expect(p.toJson()['color'], '#FF8A3D');
    });

    test('color is null when the server omits it', () {
      final p = Project.fromJson({'id': 'p1', 'name': 'No color'});
      expect(p.color, isNull);
    });

    test('copyWith preserves and overrides color', () {
      const p = Project(
        id: 'p1',
        name: 'X',
        budget: 0,
        currency: 'USD',
        status: 'active',
        color: '#111111',
      );
      expect(p.copyWith().color, '#111111');
      expect(p.copyWith(color: '#222222').color, '#222222');
    });
  });

  group('BudgetsDao local project create with color', () {
    test('stores the color + carries it in the outbox payload', () async {
      final dao = await _freshDao();
      final project =
          await dao.applyLocalProjectCreate('Marketing', color: '#3DD68C');

      expect(project.color, '#3DD68C');

      final stored = await dao.getProject(project.id);
      expect(stored!.color, '#3DD68C');

      final outbox = await dao.readBudgetsOutbox();
      expect(outbox.first.payload['color'], '#3DD68C');
    });

    test('omits color from the payload when null', () async {
      final dao = await _freshDao();
      final project = await dao.applyLocalProjectCreate('Plain');
      expect(project.color, isNull);

      final outbox = await dao.readBudgetsOutbox();
      expect(outbox.first.payload.containsKey('color'), isFalse);

      final stored = await dao.getProject(project.id);
      expect(stored!.color, isNull);
    });
  });

  group('BudgetsDao local project update with color', () {
    test('update bumps dirty + enqueues the color patch', () async {
      final dao = await _freshDao();
      final p = await dao.applyLocalProjectCreate('Proj');
      final updated = await dao.applyLocalProjectUpdate(p.id, color: '#5AA9FF');
      expect(updated!.color, '#5AA9FF');

      final stored = await dao.getProject(p.id);
      expect(stored!.color, '#5AA9FF');

      final outbox = await dao.readBudgetsOutbox();
      final updateItem =
          outbox.firstWhere((o) => o.op == BudgetsOutboxOp.update);
      expect(updateItem.entity, kProjectEntity);
      expect(updateItem.payload['color'], '#5AA9FF');
      expect(updateItem.payload['id'], p.id);
    });

    test('name + color can be set together', () async {
      final dao = await _freshDao();
      final p = await dao.applyLocalProjectCreate('Old');
      final updated =
          await dao.applyLocalProjectUpdate(p.id, name: 'New', color: '#FFB020');
      expect(updated!.name, 'New');
      expect(updated.color, '#FFB020');

      final outbox = await dao.readBudgetsOutbox();
      final updateItem =
          outbox.firstWhere((o) => o.op == BudgetsOutboxOp.update);
      expect(updateItem.payload['name'], 'New');
      expect(updateItem.payload['color'], '#FFB020');
    });
  });

  group('BudgetsDao upsertProjectFromServer with color', () {
    test('writes the server color into the clean cache row', () async {
      final dao = await _freshDao();
      await dao.upsertProjectFromServer(
        _serverProject(id: 'srv', name: 'From server', color: '#FF5C5C'),
        serverUpdatedAt: '2026-06-06T11:00:00Z',
      );
      final stored = await dao.getProject('srv');
      expect(stored!.color, '#FF5C5C');
      expect(await dao.dirtyProjectIds(), isEmpty);
    });
  });

  group('v3 → v4 migration', () {
    test('ALTER TABLE adds color to a pre-existing table + is idempotent',
        () async {
      // A pristine DB with a v3-shaped project_cache (no `color` column).
      final db = await databaseFactoryFfi.openDatabase(
        'file:projcolormig${_dbCounter++}?mode=memory&cache=shared',
        options: OpenDatabaseOptions(singleInstance: false),
      );
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

      Future<bool> columnExists() async {
        final cols = await db.rawQuery("PRAGMA table_info('project_cache')");
        return cols.any((c) => c['name'] == 'color');
      }

      expect(await columnExists(), isFalse);

      await migrateAppDb(db, 3, 4);
      expect(await columnExists(), isTrue);

      // Re-running the migration must not throw (idempotent).
      await migrateAppDb(db, 3, 4);
      expect(await columnExists(), isTrue);

      await db.close();
    });
  });
}
