/// DAO-level tests for the document cache as a SYNC SOURCE (app_db v7).
///
/// Verifies:
///   • The v7 schema carries the sync columns (dirty/deleted/last_synced_at/
///     base_updated_at) AND a v6→v7 migration adds them idempotently to an
///     existing read-through cache without losing data.
///   • Local-first create enqueues a create op + a dirty cache row.
///   • Local-first edit bumps dirty + carries base_updated_at + enqueues update.
///   • Local-first delete tombstones + enqueues a delete; listDocs hides it.
///   • markPushed clears dirty (re-bases the clock); a pushed tombstone is
///     hard-removed.
///   • LRU eviction never evicts a dirty/tombstoned row (un-pushed user data).
library;

import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/document_cache_dao.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

int _dbCounter = 0;

Future<DocumentCacheDao> _freshDao({
  String Function()? now,
  int budgetBytes = kDocumentCacheBudgetBytes,
}) async {
  final db = await databaseFactoryFfi.openDatabase(
    'file:docdaomem${_dbCounter++}?mode=memory&cache=shared',
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      singleInstance: false,
      onCreate: (db, v) async => createAppDbSchema(db),
    ),
  );
  return DocumentCacheDao(db, now: now, budgetBytes: budgetBytes);
}

void main() {
  setUpAll(() => sqfliteFfiInit());

  group('app_db v7 schema + migration', () {
    test('fresh schema has the four sync columns on document_cache', () async {
      final db = await databaseFactoryFfi.openDatabase(
        'file:docschema${_dbCounter++}?mode=memory&cache=shared',
        options: OpenDatabaseOptions(
          version: kAppDbVersion,
          singleInstance: false,
          onCreate: (db, v) async => createAppDbSchema(db),
        ),
      );
      final cols = await db.rawQuery("PRAGMA table_info('document_cache')");
      final names = cols.map((c) => c['name']).toSet();
      expect(names, containsAll(
          ['dirty', 'deleted', 'last_synced_at', 'base_updated_at']));
      // The LRU columns are retained.
      expect(names, containsAll(['byte_size', 'cached_at']));
    });

    test('v6→v7 migration adds sync columns to an existing cache + keeps data',
        () async {
      // A REAL temp file (not in-memory): an in-memory cache=shared DB is
      // destroyed when its last connection closes, so the v7 re-open would not
      // see the v6 tables. A file survives the close → the migration actually
      // upgrades the v6 schema in place.
      final dir = await Directory.systemTemp.createTemp('lz_docmigrate');
      final path = '${dir.path}/migrate.db';
      addTearDown(() => dir.delete(recursive: true));
      // Open at v6 with the OLD document_cache shape (no sync columns) + a row.
      final v6 = await databaseFactoryFfi.openDatabase(
        path,
        options: OpenDatabaseOptions(
          version: 6,
          singleInstance: false,
          onCreate: (db, v) async {
            await db.execute('''
              CREATE TABLE document_cache (
                kind TEXT NOT NULL, id TEXT NOT NULL, name TEXT,
                payload TEXT, bytes BLOB, updated_at TEXT,
                byte_size INTEGER NOT NULL DEFAULT 0, cached_at TEXT NOT NULL,
                PRIMARY KEY (kind, id)
              )''');
            await db.execute('''
              CREATE TABLE outbox (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, op TEXT NOT NULL,
                entity TEXT NOT NULL, entity_id TEXT NOT NULL, payload TEXT,
                created_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0
              )''');
            await db.execute('''
              CREATE TABLE document_list_cache (
                kind TEXT PRIMARY KEY, items TEXT NOT NULL, cached_at TEXT NOT NULL
              )''');
            await db.execute('''
              CREATE TABLE sync_state (entity TEXT PRIMARY KEY, cursor TEXT)''');
            await db.execute('''
              CREATE TABLE conflicts (id TEXT, field TEXT, local TEXT,
                server TEXT, at TEXT)''');
          },
        ),
      );
      await v6.insert('document_cache', {
        'kind': 'sheets',
        'id': 'old1',
        'name': 'Pre-existing',
        'payload': '{"a":1}',
        'byte_size': 7,
        'cached_at': '2026-06-19T00:00:00Z',
      });
      await v6.close();

      // Re-open at the current version → migrateAppDb runs v6→v7.
      final v7 = await databaseFactoryFfi.openDatabase(
        path,
        options: OpenDatabaseOptions(
          version: kAppDbVersion,
          singleInstance: false,
          onUpgrade: migrateAppDb,
        ),
      );
      final cols = await v7.rawQuery("PRAGMA table_info('document_cache')");
      final names = cols.map((c) => c['name']).toSet();
      expect(names, containsAll(
          ['dirty', 'deleted', 'last_synced_at', 'base_updated_at']));

      // Pre-existing data survived; defaults applied.
      final dao = DocumentCacheDao(v7);
      final doc = await dao.getDoc('sheets', 'old1');
      expect(doc, isNotNull);
      expect(doc!.name, 'Pre-existing');
      expect(doc.dirty, isFalse);
      expect(doc.deleted, isFalse);

      // Re-running the migration is idempotent (no throw).
      await migrateAppDb(v7, 6, kAppDbVersion);
      await v7.close();
    });
  });

  group('local-first mutations', () {
    test('create enqueues a create op + a dirty cache row', () async {
      final dao = await _freshDao();
      final id = await dao.applyLocalCreate(
          kind: 'sheets', name: 'Offline doc', payloadJson: '{"x":1}');
      final queue = await dao.readOutbox();
      expect(queue, hasLength(1));
      expect(queue.first.op, DocOutboxOp.create);
      expect(queue.first.kind, 'sheets');
      expect(queue.first.docId, id);
      final doc = await dao.getDoc('sheets', id);
      expect(doc!.dirty, isTrue);
      expect(doc.payload['x'], 1);
      expect(await dao.dirtyIds('sheets'), contains(id));
    });

    test('a supplied client id is used verbatim (idempotent replay)', () async {
      final dao = await _freshDao();
      final id = await dao.applyLocalCreate(
          kind: 'docs', id: 'fixed-uuid', name: 'D');
      expect(id, 'fixed-uuid');
      expect((await dao.readOutbox()).first.payload['id'], 'fixed-uuid');
    });

    test('edit bumps dirty, carries base_updated_at, enqueues update', () async {
      final dao = await _freshDao();
      await dao.putServerDoc(
          kind: 'sheets', id: 'e1', name: 'E', payloadJson: '{"a":1}',
          updatedAt: '2026-06-20T09:00:00Z');
      await dao.applyLocalEdit(
          kind: 'sheets',
          id: 'e1',
          name: 'E',
          payloadJson: '{"a":2}',
          baseUpdatedAt: '2026-06-20T09:00:00Z');
      final doc = await dao.getDoc('sheets', 'e1');
      expect(doc!.dirty, isTrue);
      expect(doc.payload['a'], 2);
      expect(doc.baseUpdatedAt, '2026-06-20T09:00:00Z');
      final upd = (await dao.readOutbox()).where((o) => o.op == DocOutboxOp.update);
      expect(upd, hasLength(1));
      expect(upd.first.payload['base_updated_at'], '2026-06-20T09:00:00Z');
    });

    test('edit of an un-cached doc inserts a fresh dirty row (no loss)', () async {
      final dao = await _freshDao();
      // The editor opened a doc cold and saved — no prior cache row.
      await dao.applyLocalEdit(
          kind: 'sheets', id: 'cold', name: 'Cold', payloadJson: '{"a":5}');
      final doc = await dao.getDoc('sheets', 'cold');
      expect(doc, isNotNull);
      expect(doc!.dirty, isTrue);
      expect(doc.payload['a'], 5);
    });

    test('delete tombstones + enqueues a delete; listDocs hides it', () async {
      final dao = await _freshDao();
      await dao.putServerDoc(kind: 'sheets', id: 'del1', name: 'Bye');
      final ok = await dao.applyLocalDelete('sheets', 'del1');
      expect(ok, isTrue);
      final doc = await dao.getDoc('sheets', 'del1');
      expect(doc!.deleted, isTrue);
      expect(doc.dirty, isTrue);
      expect((await dao.listDocs('sheets')).map((d) => d.id),
          isNot(contains('del1')));
      expect((await dao.readOutbox()).any((o) => o.op == DocOutboxOp.delete),
          isTrue);
    });

    test('markPushed clears dirty + re-bases the clock', () async {
      final dao = await _freshDao();
      await dao.applyLocalEdit(
          kind: 'sheets', id: 'm1', name: 'M', payloadJson: '{"a":1}');
      await dao.markPushed(
          kind: 'sheets', id: 'm1', updatedAt: '2026-06-20T12:00:00Z');
      final doc = await dao.getDoc('sheets', 'm1');
      expect(doc!.dirty, isFalse);
      expect(doc.updatedAt, '2026-06-20T12:00:00Z');
      expect(doc.baseUpdatedAt, '2026-06-20T12:00:00Z');
    });

    test('committing a pushed tombstone hard-removes the row', () async {
      final dao = await _freshDao();
      await dao.putServerDoc(kind: 'sheets', id: 't1', name: 'T');
      await dao.applyLocalDelete('sheets', 't1');
      final seq = (await dao.readOutbox()).first.seq;
      await dao.commitPush(seq, docEntityId('sheets', 't1'));
      expect(await dao.getDoc('sheets', 't1'), isNull);
      expect(await dao.readOutbox(), isEmpty);
    });
  });

  group('eviction safety', () {
    test('LRU eviction never drops a dirty row', () async {
      // Tiny budget so any second row triggers eviction.
      final dao = await _freshDao(budgetBytes: 10);
      // Dirty (un-pushed) row first — must be protected.
      await dao.applyLocalCreate(
          kind: 'sheets', id: 'dirty', name: 'Dirty', payloadJson: '{"big":"xxxxxxxxxx"}');
      // A clean server row that blows the budget — eviction kicks in.
      await dao.putServerDoc(
          kind: 'sheets', id: 'clean', name: 'Clean',
          payloadJson: '{"big":"yyyyyyyyyy"}');
      // The dirty row survives; only the clean one may be evicted.
      expect(await dao.getDoc('sheets', 'dirty'), isNotNull);
    });
  });
}
