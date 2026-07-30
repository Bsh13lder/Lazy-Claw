// DAO round-trip for `recur_until` (the recurring series' end date).
//
// The field follows the `recurring` convention exactly: a value sets, the ''
// clear sentinel rides the outbox patch verbatim (the server clears on empty),
// null means "field untouched". A missed mapping point here is the "metadata
// shape mismatch = phantom loss" class — the field silently drops and LWW
// propagates the loss — so create payload, update patch, cache row and the
// server-upsert path are each pinned against a real SQLite engine.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

int _dbCounter = 0;

/// Isolated in-memory DB per call (mirrors task_dao_due_clear_test.dart).
Future<TaskDao> _freshDao() async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:recuruntilmem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return TaskDao(db);
}

/// Drain the outbox so the next readOutbox() returns only the op under test.
Future<void> _drainOutbox(TaskDao dao) async {
  for (final o in await dao.readOutbox()) {
    await dao.deleteOutboxItem(o.seq);
  }
}

void main() {
  setUpAll(() => sqfliteFfiInit());

  group('TaskDao — recur_until round-trip', () {
    test('create persists recur_until and rides it in the create payload',
        () async {
      final dao = await _freshDao();
      final created = await dao.applyLocalCreate(
        'Water plants',
        id: 't-ru-create',
        recurring: '0 9 * * *',
        recurUntil: '2026-09-30',
      );

      expect(created.recurUntil, '2026-09-30');
      final row = await dao.getById('t-ru-create');
      expect(row!.recurUntil, '2026-09-30', reason: 'cache row must carry it');

      final outbox = await dao.readOutbox();
      expect(outbox, hasLength(1));
      expect(outbox.single.op, OutboxOp.create);
      expect(outbox.single.payload['recur_until'], '2026-09-30',
          reason: 'an offline create must not lose the series end on push');
    });

    test('a create without recur_until omits the key from the payload',
        () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('Forever', id: 't-ru-none',
          recurring: '0 9 * * *');
      final outbox = await dao.readOutbox();
      expect(outbox.single.payload.containsKey('recur_until'), isFalse);
    });

    test('update sets recur_until in the cache AND the outbox patch', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('Repeats', id: 't-ru-set',
          recurring: '0 9 * * 1');
      await _drainOutbox(dao);

      final updated =
          await dao.applyLocalUpdate('t-ru-set', recurUntil: '2027-01-31');

      expect(updated!.recurUntil, '2027-01-31');
      expect((await dao.getById('t-ru-set'))!.recurUntil, '2027-01-31');
      final outbox = await dao.readOutbox();
      expect(outbox.single.op, OutboxOp.update);
      expect(outbox.single.payload['recur_until'], '2027-01-31');
    });

    test('the "" clear sentinel rides the patch verbatim (server clears on '
        'empty) and empties the cache value', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('Ends someday', id: 't-ru-clear',
          recurring: '0 9 * * *', recurUntil: '2026-12-31');
      await _drainOutbox(dao);

      final updated = await dao.applyLocalUpdate('t-ru-clear', recurUntil: '');

      // Mirrors the `recurring` convention: '' is stored/returned as empty —
      // every reader treats a blank value as "repeats forever".
      expect(updated!.recurUntil ?? '', isEmpty);
      expect((await dao.getById('t-ru-clear'))!.recurUntil ?? '', isEmpty);

      final outbox = await dao.readOutbox();
      expect(outbox.single.payload.containsKey('recur_until'), isTrue,
          reason: 'a cleared recur_until must be included, not dropped');
      expect(outbox.single.payload['recur_until'], '');
    });

    test('passing null leaves recur_until untouched and omits it from the '
        'patch', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('Keep me', id: 't-ru-keep',
          recurring: '0 9 * * *', recurUntil: '2026-11-01');
      await _drainOutbox(dao);

      final updated = await dao.applyLocalUpdate('t-ru-keep', title: 'renamed');

      expect(updated!.recurUntil, '2026-11-01');
      expect((await dao.getById('t-ru-keep'))!.recurUntil, '2026-11-01');
      final outbox = await dao.readOutbox();
      expect(outbox.single.payload.containsKey('recur_until'), isFalse);
    });

    test('upsertFromServer round-trips recur_until through the cache row',
        () async {
      final dao = await _freshDao();
      final serverTask = Task.fromJson({
        'id': 's-ru-1',
        'user_id': 'u1',
        'title': 'From server',
        'priority': 'medium',
        'status': 'todo',
        'owner': 'user',
        'recurring': '0 9 * * *',
        'recur_until': '2026-10-15',
        'nag_count': 0,
        'created_at': '2026-07-01T10:00:00Z',
      });

      await dao.upsertFromServer(serverTask,
          serverUpdatedAt: '2026-07-01T10:00:00Z');

      expect((await dao.getById('s-ru-1'))!.recurUntil, '2026-10-15');
    });

    test('reenqueueOrphanedCreates preserves recur_until in the healed '
        'create payload', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('Stranded', id: 't-ru-orphan',
          recurring: '0 9 * * *', recurUntil: '2026-08-31');
      // Simulate an older build silently draining the create op.
      await _drainOutbox(dao);

      final healed = await dao.reenqueueOrphanedCreates();

      expect(healed, 1);
      final outbox = await dao.readOutbox();
      expect(outbox.single.op, OutboxOp.create);
      expect(outbox.single.payload['recur_until'], '2026-08-31');
    });
  });
}
