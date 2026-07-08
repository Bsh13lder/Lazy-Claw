import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

int _dbCounter = 0;

/// Spin up a real in-memory SQLite (via ffi) with the production schema, so the
/// DAO logic is verified against the actual engine — no hand-rolled fake DB.
/// Each call gets an ISOLATED in-memory DB (`singleInstance: false` + a unique
/// path) so state never bleeds between tests.
Future<TaskDao> _freshDao() async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:memdb${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return TaskDao(db);
}

Task _serverTask({
  String id = 's1',
  String title = 'Server task',
  String status = 'todo',
  String createdAt = '2026-06-05T10:00:00Z',
}) =>
    Task(
      id: id,
      userId: 'u1',
      title: title,
      priority: 'medium',
      status: status,
      owner: 'user',
      nagCount: 0,
      createdAt: createdAt,
    );

void main() {
  setUpAll(() {
    sqfliteFfiInit();
  });

  // ── Self-heal stranded creates (orphan recovery — mirrors BudgetsDao) ───────
  group('reenqueueOrphanedCreates', () {
    test('re-enqueues a stranded task create (dropped op, never synced)',
        () async {
      final dao = await _freshDao();
      // An offline-created task: dirty=1, last_synced_at=null, create queued.
      await dao.applyLocalCreate('ZZsynctest',
          id: 'task-orphan', priority: 'medium', category: 'Project');
      // Simulate the create op being dropped (drained by an older build / outage).
      for (final o in await dao.readOutbox()) {
        await dao.deleteOutboxItem(o.seq);
      }
      expect(await dao.readOutbox(), isEmpty);

      final healed = await dao.reenqueueOrphanedCreates();

      expect(healed, 1);
      final outbox = await dao.readOutbox();
      expect(outbox.length, 1);
      final op = outbox.single;
      expect(op.op, OutboxOp.create);
      expect(op.entity, kTaskEntity);
      expect(op.entityId, 'task-orphan');
      expect(op.payload['id'], 'task-orphan');
      expect(op.payload['title'], 'ZZsynctest');
      expect(op.payload['priority'], 'medium');
      expect(op.payload['category'], 'Project');
    });

    test('does NOT duplicate when a pending op already exists', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('still queued', id: 't1');
      // Op is still queued → not an orphan.
      final healed = await dao.reenqueueOrphanedCreates();
      expect(healed, 0);
      expect((await dao.readOutbox()).length, 1);
    });

    test('skips a row already flagged create_rejected (no infinite loop)',
        () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('bad', id: 't-bad');
      for (final o in await dao.readOutbox()) {
        await dao.deleteOutboxItem(o.seq);
      }
      await dao.logConflict(
          id: 't-bad', field: 'create_rejected', local: 'HTTP 400', server: null);

      final healed = await dao.reenqueueOrphanedCreates();

      expect(healed, 0);
      expect(await dao.readOutbox(), isEmpty);
    });

    test('skips a synced row whose UPDATE op was drained (last_synced_at set)',
        () async {
      final dao = await _freshDao();
      // Pulled from server → last_synced_at set, dirty=0.
      await dao.upsertFromServer(_serverTask(id: 't-synced'));
      // A local edit → dirty=1 but last_synced_at stays (it DID reach server once).
      await dao.applyLocalUpdate('t-synced', title: 'edited offline');
      for (final o in await dao.readOutbox()) {
        await dao.deleteOutboxItem(o.seq);
      }

      final healed = await dao.reenqueueOrphanedCreates();

      // Not a stranded CREATE — it exists server-side; the next pull reconciles it.
      expect(healed, 0);
    });

    test('idempotent — a second heal does not double-enqueue', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('orphan', id: 't-x');
      for (final o in await dao.readOutbox()) {
        await dao.deleteOutboxItem(o.seq);
      }

      expect(await dao.reenqueueOrphanedCreates(), 1);
      // Second call: the row now has a pending op again → no double-enqueue.
      expect(await dao.reenqueueOrphanedCreates(), 0);
      expect((await dao.readOutbox()).length, 1);
    });

    test('clearCreateRejectedConflicts unblocks a rejected orphan (force-retry)',
        () async {
      final dao = await _freshDao();
      // A stranded orphan flagged create_rejected (transient outage rejection).
      await dao.applyLocalCreate('reserva-like', id: 't-rej');
      for (final o in await dao.readOutbox()) {
        await dao.deleteOutboxItem(o.seq);
      }
      await dao.logConflict(
          id: 't-rej', field: 'create_rejected', local: 'HTTP 401', server: null);

      // Routine heal skips it (still excluded).
      expect(await dao.reenqueueOrphanedCreates(), 0);

      // Force-retry: clear markers, then the heal re-queues it.
      final cleared = await dao.clearCreateRejectedConflicts();
      expect(cleared, 1);
      expect(await dao.reenqueueOrphanedCreates(), 1);
      final op = (await dao.readOutbox()).single;
      expect(op.op, OutboxOp.create);
      expect(op.entityId, 't-rej');
    });
  });

  group('TaskDao local create', () {
    test('mints a UUID, stores dirty row, and enqueues a create', () async {
      final dao = await _freshDao();
      final task = await dao.applyLocalCreate('Buy milk');

      expect(task.id, isNotEmpty);
      expect(task.title, 'Buy milk');
      expect(task.status, 'todo');

      final stored = await dao.getById(task.id);
      expect(stored, isNotNull);

      final dirty = await dao.dirtyIds();
      expect(dirty, contains(task.id));

      final outbox = await dao.readOutbox();
      expect(outbox, hasLength(1));
      expect(outbox.first.op, OutboxOp.create);
      expect(outbox.first.entityId, task.id);
      expect(outbox.first.payload['id'], task.id);
      expect(outbox.first.payload['title'], 'Buy milk');
    });

    test('honours a caller-supplied id (idempotent replay)', () async {
      final dao = await _freshDao();
      final task = await dao.applyLocalCreate('Pinned', id: 'fixed-id');
      expect(task.id, 'fixed-id');
      final outbox = await dao.readOutbox();
      expect(outbox.first.payload['id'], 'fixed-id');
    });

    test('passes optional fields into the outbox payload', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate(
        'Dentist',
        priority: 'high',
        dueDate: '2026-06-20',
        category: 'health',
      );
      final outbox = await dao.readOutbox();
      expect(outbox.first.payload['priority'], 'high');
      expect(outbox.first.payload['due_date'], '2026-06-20');
      expect(outbox.first.payload['category'], 'health');
    });

    test('persists description + steps and queues them in the create payload',
        () async {
      final dao = await _freshDao();
      const stepsJson =
          '[{"id":"s1","title":"First","done":false},'
          '{"id":"s2","title":"Second","done":true}]';
      final task = await dao.applyLocalCreate(
        'With notes + subtasks',
        description: 'Some notes',
        steps: stepsJson,
      );
      expect(task.description, 'Some notes');
      expect(task.steps, stepsJson);

      // Round-trips back out of the cache verbatim, including the parsed list.
      final stored = await dao.getById(task.id);
      expect(stored!.description, 'Some notes');
      expect(stored.steps, stepsJson);
      expect(stored.subtasks, hasLength(2));
      expect(stored.subtasks.last.done, isTrue);

      // Both ride the create outbox payload for server replay.
      final outbox = await dao.readOutbox();
      expect(outbox.first.op, OutboxOp.create);
      expect(outbox.first.payload['description'], 'Some notes');
      expect(outbox.first.payload['steps'], stepsJson);
    });
  });

  group('TaskDao local list excludes deletes', () {
    test('list() hides tombstoned tasks', () async {
      final dao = await _freshDao();
      final a = await dao.applyLocalCreate('A');
      await dao.applyLocalCreate('B');
      await dao.applyLocalDelete(a.id);

      final tasks = await dao.list();
      expect(tasks.map((t) => t.title), ['B']);
    });
  });

  group('TaskDao local update / complete / delete', () {
    test('update bumps dirty + enqueues an update with the patch', () async {
      final dao = await _freshDao();
      final t = await dao.applyLocalCreate('Old title');
      await dao.readOutbox(); // create row exists

      final updated = await dao.applyLocalUpdate(t.id, title: 'New title');
      expect(updated!.title, 'New title');

      final stored = await dao.getById(t.id);
      expect(stored!.title, 'New title');

      final outbox = await dao.readOutbox();
      final updateItem = outbox.firstWhere((o) => o.op == OutboxOp.update);
      expect(updateItem.payload['title'], 'New title');
      expect(updateItem.payload['id'], t.id);
    });

    test('update persists sub-task steps + enqueues them in the patch',
        () async {
      final dao = await _freshDao();
      final t = await dao.applyLocalCreate('Has subtasks');
      const stepsJson =
          '[{"id":"s1","title":"First","done":false},'
          '{"id":"s2","title":"Second","done":true}]';

      final updated = await dao.applyLocalUpdate(t.id, steps: stepsJson);
      expect(updated!.steps, stepsJson);

      // Round-trips back out of the cache verbatim.
      final stored = await dao.getById(t.id);
      expect(stored!.steps, stepsJson);
      expect(stored.subtasks, hasLength(2));
      expect(stored.subtasks.last.done, isTrue);

      // The steps payload is queued for the server in the update op.
      final outbox = await dao.readOutbox();
      final updateItem = outbox.firstWhere((o) => o.op == OutboxOp.update);
      expect(updateItem.payload['steps'], stepsJson);
    });

    test('complete marks done + enqueues a complete', () async {
      final dao = await _freshDao();
      final t = await dao.applyLocalCreate('Finish me');
      final done = await dao.applyLocalComplete(t.id);
      expect(done!.status, 'done');

      final stored = await dao.getById(t.id);
      expect(stored!.status, 'done');

      final outbox = await dao.readOutbox();
      expect(outbox.any((o) => o.op == OutboxOp.complete), isTrue);
    });

    test('delete tombstones + enqueues a delete', () async {
      final dao = await _freshDao();
      final t = await dao.applyLocalCreate('Remove me');
      final ok = await dao.applyLocalDelete(t.id);
      expect(ok, isTrue);

      expect(await dao.getById(t.id), isNotNull); // tombstone still present
      expect((await dao.list()).map((e) => e.id), isNot(contains(t.id)));

      final outbox = await dao.readOutbox();
      expect(outbox.any((o) => o.op == OutboxOp.delete), isTrue);
    });

    test('update/complete/delete on a missing id is a no-op', () async {
      final dao = await _freshDao();
      expect(await dao.applyLocalUpdate('nope', title: 'x'), isNull);
      expect(await dao.applyLocalComplete('nope'), isNull);
      expect(await dao.applyLocalDelete('nope'), isFalse);
      expect(await dao.readOutbox(), isEmpty);
    });
  });

  group('TaskDao outbox replay ordering', () {
    test('reads outbox in seq (insertion) order', () async {
      final dao = await _freshDao();
      final t = await dao.applyLocalCreate('A');
      await dao.applyLocalUpdate(t.id, title: 'A2');
      await dao.applyLocalComplete(t.id);

      final outbox = await dao.readOutbox();
      expect(outbox.map((o) => o.op),
          [OutboxOp.create, OutboxOp.update, OutboxOp.complete]);
      // seq is strictly increasing.
      for (var i = 1; i < outbox.length; i++) {
        expect(outbox[i].seq, greaterThan(outbox[i - 1].seq));
      }
    });

    test('deleteOutboxItem removes only the targeted row', () async {
      final dao = await _freshDao();
      final t = await dao.applyLocalCreate('A');
      await dao.applyLocalComplete(t.id);
      final outbox = await dao.readOutbox();
      await dao.deleteOutboxItem(outbox.first.seq);
      final remaining = await dao.readOutbox();
      expect(remaining, hasLength(1));
      expect(remaining.first.op, OutboxOp.complete);
    });
  });

  group('TaskDao clearDirty', () {
    test('clears dirty flag for a live task', () async {
      final dao = await _freshDao();
      final t = await dao.applyLocalCreate('A');
      await dao.clearDirty(t.id);
      expect(await dao.dirtyIds(), isEmpty);
      expect(await dao.getById(t.id), isNotNull);
    });

    test('hard-removes a tombstone once its delete has pushed', () async {
      final dao = await _freshDao();
      final t = await dao.applyLocalCreate('A');
      await dao.applyLocalDelete(t.id);
      await dao.clearDirty(t.id);
      expect(await dao.getById(t.id), isNull);
    });
  });

  group('TaskDao upsertFromServer + tombstone', () {
    test('writes a clean server row', () async {
      final dao = await _freshDao();
      await dao.upsertFromServer(
        _serverTask(id: 'srv', title: 'From server'),
        serverUpdatedAt: '2026-06-05T11:00:00Z',
      );
      final stored = await dao.getById('srv');
      expect(stored!.title, 'From server');
      expect(await dao.dirtyIds(), isEmpty);
      final row = await dao.getRow('srv');
      expect(row!['updated_at'], '2026-06-05T11:00:00Z');
    });

    test('applyServerDelete tombstones an existing row', () async {
      final dao = await _freshDao();
      await dao.upsertFromServer(_serverTask(id: 'srv'));
      await dao.applyServerDelete('srv');
      expect((await dao.list()).map((e) => e.id), isNot(contains('srv')));
    });
  });

  group('TaskDao cursor + conflicts', () {
    test('cursor round-trips per entity', () async {
      final dao = await _freshDao();
      expect(await dao.getCursor(), isNull);
      await dao.setCursor('2026-06-05T12:00:00Z');
      expect(await dao.getCursor(), '2026-06-05T12:00:00Z');
      await dao.setCursor('2026-06-05T13:00:00Z');
      expect(await dao.getCursor(), '2026-06-05T13:00:00Z');
    });

    test('logs and reads conflicts (never silently dropped)', () async {
      final dao = await _freshDao();
      await dao.logConflict(
        id: 't1',
        field: 'title',
        local: 'Local title',
        server: 'Server title',
        at: '2026-06-05T12:00:00Z',
      );
      final conflicts = await dao.readConflicts();
      expect(conflicts, hasLength(1));
      expect(conflicts.first.field, 'title');
      expect(conflicts.first.local, 'Local title');
      expect(conflicts.first.server, 'Server title');
    });

    test('M1: dedups an identical conflict (incl. null server)', () async {
      final dao = await _freshDao();
      // Log the same (id, field, local, server=null) twice.
      await dao.logConflict(id: 't1', field: 'title', local: 'A', server: null);
      await dao.logConflict(id: 't1', field: 'title', local: 'A', server: null);
      // A genuinely different server value is a distinct conflict.
      await dao.logConflict(id: 't1', field: 'title', local: 'A', server: 'B');
      final conflicts = await dao.readConflicts();
      expect(conflicts, hasLength(2));
    });
  });

  group('TaskDao push-commit + retry bookkeeping', () {
    test('commitPush atomically dequeues + clears dirty; idempotent on replay',
        () async {
      final dao = await _freshDao();
      final t = await dao.applyLocalCreate('A', id: 'cp1');
      final seq = (await dao.readOutbox()).first.seq;

      await dao.commitPush(seq, t.id);
      expect(await dao.readOutbox(), isEmpty);
      expect(await dao.dirtyIds(), isEmpty);
      expect(await dao.getById(t.id), isNotNull);

      // Replay (crash-retry) must be a safe no-op.
      await dao.commitPush(seq, t.id);
      expect(await dao.dirtyIds(), isEmpty);
    });

    test('commitPush hard-removes a pushed tombstone', () async {
      final dao = await _freshDao();
      final t = await dao.applyLocalCreate('A', id: 'cp2');
      await dao.applyLocalDelete(t.id);
      final delSeq = (await dao.readOutbox())
          .firstWhere((o) => o.op == OutboxOp.delete)
          .seq;
      await dao.commitPush(delSeq, t.id);
      expect(await dao.getById(t.id), isNull);
    });

    test('bumpOutboxAttempts increments + returns the new count', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate('A', id: 'ba1');
      final seq = (await dao.readOutbox()).first.seq;
      expect(await dao.bumpOutboxAttempts(seq), 1);
      expect(await dao.bumpOutboxAttempts(seq), 2);
      expect((await dao.readOutbox()).first.attempts, 2);
    });

    test('deadLetterOutboxItem drops the row but leaves the cache dirty',
        () async {
      final dao = await _freshDao();
      final t = await dao.applyLocalCreate('A', id: 'dl1');
      final seq = (await dao.readOutbox()).first.seq;
      await dao.deadLetterOutboxItem(seq);
      expect(await dao.readOutbox(), isEmpty);
      // Cache row stays dirty so the next pull re-establishes server truth.
      expect(await dao.dirtyIds(), contains(t.id));
    });

    test('deleteOutboxForEntity removes every queued op for one id', () async {
      final dao = await _freshDao();
      final t = await dao.applyLocalCreate('A', id: 'de1');
      await dao.applyLocalUpdate(t.id, title: 'A2');
      await dao.applyLocalComplete(t.id);
      // An unrelated entity's op must survive.
      await dao.applyLocalCreate('Other', id: 'other');
      final removed = await dao.deleteOutboxForEntity(t.id);
      expect(removed, 3);
      final remaining = await dao.readOutbox();
      expect(remaining.every((o) => o.entityId == 'other'), isTrue);
    });
  });
}
