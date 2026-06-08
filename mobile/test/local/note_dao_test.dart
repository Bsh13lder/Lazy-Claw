import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/note_dao.dart';
import 'package:lazyclaw_mobile/models/note.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

int _dbCounter = 0;

/// Spin up a real in-memory SQLite (via ffi) with the production schema, so the
/// DAO logic is verified against the actual engine — no hand-rolled fake DB.
/// Each call gets an ISOLATED in-memory DB so state never bleeds between tests.
Future<NoteDao> _freshDao({String Function()? now}) async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:notedaomem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return NoteDao(db, now: now);
}

Note _serverNote({
  String id = 's1',
  String? title = 'Server note',
  String content = 'Server content',
  List<String> tags = const ['work'],
  int importance = 0,
  bool pinned = false,
  String createdAt = '2026-06-05T10:00:00Z',
  String updatedAt = '2026-06-05T10:00:00Z',
}) =>
    Note(
      id: id,
      title: title,
      content: content,
      tags: tags,
      importance: importance,
      pinned: pinned,
      createdAt: createdAt,
      updatedAt: updatedAt,
    );

void main() {
  setUpAll(() {
    sqfliteFfiInit();
  });

  group('NoteDao local create', () {
    test('mints a UUID, stores dirty row, and enqueues a create', () async {
      final dao = await _freshDao();
      final note = await dao.applyLocalCreate(content: 'Buy milk');

      expect(note.id, isNotEmpty);
      expect(note.content, 'Buy milk');

      final stored = await dao.getById(note.id);
      expect(stored, isNotNull);

      final dirty = await dao.dirtyIds();
      expect(dirty, contains(note.id));

      final outbox = await dao.readOutbox();
      expect(outbox, hasLength(1));
      expect(outbox.first.op, NoteOutboxOp.create);
      expect(outbox.first.entityId, note.id);
      expect(outbox.first.entity, kNoteEntity);
      expect(outbox.first.payload['id'], note.id);
      expect(outbox.first.payload['content'], 'Buy milk');
    });

    test('honours a caller-supplied id (idempotent replay)', () async {
      final dao = await _freshDao();
      final note =
          await dao.applyLocalCreate(content: 'Pinned', id: 'fixed-id');
      expect(note.id, 'fixed-id');
      final outbox = await dao.readOutbox();
      expect(outbox.first.payload['id'], 'fixed-id');
    });

    test('passes optional fields into the outbox payload', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate(
        content: 'Meeting notes',
        title: 'Standup',
        tags: ['work', 'meeting'],
        importance: 3,
        pinned: true,
      );
      final outbox = await dao.readOutbox();
      expect(outbox.first.payload['title'], 'Standup');
      expect(outbox.first.payload['tags'], ['work', 'meeting']);
      expect(outbox.first.payload['importance'], 3);
      expect(outbox.first.payload['pinned'], true);
    });

    test('round-trips tags + pinned through the cache row', () async {
      final dao = await _freshDao();
      final note = await dao.applyLocalCreate(
        content: 'x',
        tags: ['a', 'b', 'c'],
        pinned: true,
        importance: 2,
      );
      final stored = await dao.getById(note.id);
      expect(stored!.tags, ['a', 'b', 'c']);
      expect(stored.pinned, isTrue);
      expect(stored.importance, 2);
    });
  });

  group('NoteDao local list excludes deletes', () {
    test('list() hides tombstoned notes', () async {
      final dao = await _freshDao();
      final a = await dao.applyLocalCreate(content: 'A');
      await dao.applyLocalCreate(content: 'B');
      await dao.applyLocalDelete(a.id);

      final notes = await dao.list();
      expect(notes.map((n) => n.content), ['B']);
    });
  });

  group('NoteDao local update / delete', () {
    test('update bumps dirty + enqueues an update with the patch', () async {
      final dao = await _freshDao();
      final n = await dao.applyLocalCreate(content: 'Old', title: 'Old title');
      await dao.readOutbox(); // create row exists

      final updated =
          await dao.applyLocalUpdate(n.id, title: 'New title', content: 'New');
      expect(updated!.title, 'New title');
      expect(updated.content, 'New');

      final stored = await dao.getById(n.id);
      expect(stored!.title, 'New title');
      expect(stored.content, 'New');

      final outbox = await dao.readOutbox();
      final updateItem = outbox.firstWhere((o) => o.op == NoteOutboxOp.update);
      expect(updateItem.payload['title'], 'New title');
      expect(updateItem.payload['content'], 'New');
      expect(updateItem.payload['id'], n.id);
    });

    test('update only changes supplied fields', () async {
      final dao = await _freshDao();
      final n = await dao.applyLocalCreate(
        content: 'Body',
        title: 'Keep title',
        tags: ['keep'],
      );
      await dao.applyLocalUpdate(n.id, content: 'New body only');
      final stored = await dao.getById(n.id);
      expect(stored!.title, 'Keep title'); // untouched
      expect(stored.content, 'New body only');
      expect(stored.tags, ['keep']); // untouched
    });

    test('delete tombstones + enqueues a delete', () async {
      final dao = await _freshDao();
      final n = await dao.applyLocalCreate(content: 'Remove me');
      final ok = await dao.applyLocalDelete(n.id);
      expect(ok, isTrue);

      expect(await dao.getById(n.id), isNotNull); // tombstone still present
      expect((await dao.list()).map((e) => e.id), isNot(contains(n.id)));

      final outbox = await dao.readOutbox();
      expect(outbox.any((o) => o.op == NoteOutboxOp.delete), isTrue);
    });

    test('update/delete on a missing id is a no-op', () async {
      final dao = await _freshDao();
      expect(await dao.applyLocalUpdate('nope', title: 'x'), isNull);
      expect(await dao.applyLocalDelete('nope'), isFalse);
      expect(await dao.readOutbox(), isEmpty);
    });
  });

  group('NoteDao outbox replay ordering + scoping', () {
    test('reads outbox in seq (insertion) order', () async {
      final dao = await _freshDao();
      final n = await dao.applyLocalCreate(content: 'A');
      await dao.applyLocalUpdate(n.id, title: 'A2');
      await dao.applyLocalDelete(n.id);

      final outbox = await dao.readOutbox();
      expect(outbox.map((o) => o.op),
          [NoteOutboxOp.create, NoteOutboxOp.update, NoteOutboxOp.delete]);
      for (var i = 1; i < outbox.length; i++) {
        expect(outbox[i].seq, greaterThan(outbox[i - 1].seq));
      }
    });

    test('readOutbox is scoped to the note entity (ignores foreign rows)',
        () async {
      // Hold the DB handle so we can inject a foreign (task) outbox row into the
      // SAME shared `outbox` table the NoteDao reads from.
      final db = await databaseFactoryFfi.openDatabase(
        'file:notedaoscope${_dbCounter++}?mode=memory&cache=shared',
        options: OpenDatabaseOptions(
          version: kAppDbVersion,
          singleInstance: false,
          onCreate: (db, v) async => createAppDbSchema(db),
        ),
      );
      final dao = NoteDao(db);
      await dao.applyLocalCreate(content: 'A', id: 'note-a');
      // A task-entity row sharing the table must NOT surface in note reads.
      await db.insert('outbox', {
        'op': 'create',
        'entity': 'task',
        'entity_id': 'task-x',
        'payload': '{}',
        'created_at': '2026-06-05T10:00:00Z',
      });

      final outbox = await dao.readOutbox();
      expect(outbox.every((o) => o.entity == kNoteEntity), isTrue);
      expect(outbox.map((o) => o.entityId), ['note-a']);
      expect(await dao.outboxCount(), 1); // counts notes only
    });

    test('deleteOutboxItem removes only the targeted row', () async {
      final dao = await _freshDao();
      final n = await dao.applyLocalCreate(content: 'A');
      await dao.applyLocalUpdate(n.id, title: 'A2');
      final outbox = await dao.readOutbox();
      await dao.deleteOutboxItem(outbox.first.seq);
      final remaining = await dao.readOutbox();
      expect(remaining, hasLength(1));
      expect(remaining.first.op, NoteOutboxOp.update);
    });
  });

  group('NoteDao clearDirty', () {
    test('clears dirty flag for a live note', () async {
      final dao = await _freshDao();
      final n = await dao.applyLocalCreate(content: 'A');
      await dao.clearDirty(n.id);
      expect(await dao.dirtyIds(), isEmpty);
      expect(await dao.getById(n.id), isNotNull);
    });

    test('hard-removes a tombstone once its delete has pushed', () async {
      final dao = await _freshDao();
      final n = await dao.applyLocalCreate(content: 'A');
      await dao.applyLocalDelete(n.id);
      await dao.clearDirty(n.id);
      expect(await dao.getById(n.id), isNull);
    });
  });

  group('NoteDao upsertFromServer + tombstone', () {
    test('writes a clean server row', () async {
      final dao = await _freshDao();
      await dao.upsertFromServer(
        _serverNote(id: 'srv', content: 'From server'),
        serverUpdatedAt: '2026-06-05T11:00:00Z',
      );
      final stored = await dao.getById('srv');
      expect(stored!.content, 'From server');
      expect(await dao.dirtyIds(), isEmpty);
      final row = await dao.getRow('srv');
      expect(row!['updated_at'], '2026-06-05T11:00:00Z');
    });

    test('applyServerDelete tombstones an existing row', () async {
      final dao = await _freshDao();
      await dao.upsertFromServer(_serverNote(id: 'srv'));
      await dao.applyServerDelete('srv');
      expect((await dao.list()).map((e) => e.id), isNot(contains('srv')));
    });
  });

  group('NoteDao cursor + conflicts', () {
    test('cursor round-trips for the note entity', () async {
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
        id: 'n1',
        field: 'content',
        local: 'Local body',
        server: 'Server body',
        at: '2026-06-05T12:00:00Z',
      );
      final conflicts = await dao.readConflicts();
      expect(conflicts, hasLength(1));
      expect(conflicts.first.field, 'content');
      expect(conflicts.first.local, 'Local body');
      expect(conflicts.first.server, 'Server body');
    });

    test('dedups an identical conflict (incl. null server)', () async {
      final dao = await _freshDao();
      await dao.logConflict(
          id: 'n1', field: 'content', local: 'A', server: null);
      await dao.logConflict(
          id: 'n1', field: 'content', local: 'A', server: null);
      await dao.logConflict(id: 'n1', field: 'content', local: 'A', server: 'B');
      final conflicts = await dao.readConflicts();
      expect(conflicts, hasLength(2));
    });
  });

  group('NoteDao push-commit + retry bookkeeping', () {
    test('commitPush atomically dequeues + clears dirty; idempotent on replay',
        () async {
      final dao = await _freshDao();
      final n = await dao.applyLocalCreate(content: 'A', id: 'cp1');
      final seq = (await dao.readOutbox()).first.seq;

      await dao.commitPush(seq, n.id);
      expect(await dao.readOutbox(), isEmpty);
      expect(await dao.dirtyIds(), isEmpty);
      expect(await dao.getById(n.id), isNotNull);

      // Replay (crash-retry) must be a safe no-op.
      await dao.commitPush(seq, n.id);
      expect(await dao.dirtyIds(), isEmpty);
    });

    test('commitPush hard-removes a pushed tombstone', () async {
      final dao = await _freshDao();
      final n = await dao.applyLocalCreate(content: 'A', id: 'cp2');
      await dao.applyLocalDelete(n.id);
      final delSeq = (await dao.readOutbox())
          .firstWhere((o) => o.op == NoteOutboxOp.delete)
          .seq;
      await dao.commitPush(delSeq, n.id);
      expect(await dao.getById(n.id), isNull);
    });

    test('bumpOutboxAttempts increments + returns the new count', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate(content: 'A', id: 'ba1');
      final seq = (await dao.readOutbox()).first.seq;
      expect(await dao.bumpOutboxAttempts(seq), 1);
      expect(await dao.bumpOutboxAttempts(seq), 2);
      expect((await dao.readOutbox()).first.attempts, 2);
    });

    test('deadLetterOutboxItem drops the row but leaves the cache dirty',
        () async {
      final dao = await _freshDao();
      final n = await dao.applyLocalCreate(content: 'A', id: 'dl1');
      final seq = (await dao.readOutbox()).first.seq;
      await dao.deadLetterOutboxItem(seq);
      expect(await dao.readOutbox(), isEmpty);
      expect(await dao.dirtyIds(), contains(n.id));
    });

    test('deleteOutboxForEntity removes every queued op for one id', () async {
      final dao = await _freshDao();
      final n = await dao.applyLocalCreate(content: 'A', id: 'de1');
      await dao.applyLocalUpdate(n.id, title: 'A2');
      await dao.applyLocalUpdate(n.id, content: 'A3');
      // An unrelated entity's op must survive.
      await dao.applyLocalCreate(content: 'Other', id: 'other');
      final removed = await dao.deleteOutboxForEntity(n.id);
      expect(removed, 3);
      final remaining = await dao.readOutbox();
      expect(remaining.every((o) => o.entityId == 'other'), isTrue);
    });

    test('outboxCount counts only note rows', () async {
      final dao = await _freshDao();
      await dao.applyLocalCreate(content: 'A');
      await dao.applyLocalCreate(content: 'B');
      expect(await dao.outboxCount(), 2);
    });
  });
}
