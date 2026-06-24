import 'dart:convert';
import 'dart:typed_data';

import 'package:sqflite_sqlcipher/sqflite.dart';

import 'app_db.dart';

/// Total on-disk budget for the document cache. When a write pushes the cache
/// over this, the least-recently-cached CLEAN rows are evicted until it fits.
/// 64 MB comfortably holds many sheets/docs and a handful of PDFs without
/// letting the encrypted DB grow unbounded. Dirty/tombstoned rows are NEVER
/// evicted — they carry un-pushed user edits the sync engine still needs.
const int kDocumentCacheBudgetBytes = 64 * 1024 * 1024;

/// Outbox op kinds for documents (sheets/docs). Replayed in `seq` order.
/// PDFs only ever enqueue a `delete` (their content is import-only/immutable).
class DocOutboxOp {
  static const create = 'create';
  static const update = 'update';
  static const delete = 'delete';
}

/// The outbox `entity` tag for documents. ONE shared outbox table holds task,
/// note, budget AND document rows; the entity column keeps them from colliding.
/// The document entity_id is `"<kind>:<id>"` (composite) because a sheet and a
/// PDF can legitimately share the same id while living in different kinds — the
/// `kindOf` / `idOf` helpers split it back out for the push engine.
const String kDocEntity = 'document';

/// Build the composite outbox entity_id from a (kind, id) pair.
String docEntityId(String kind, String id) => '$kind:$id';

/// Split a composite document entity_id back into (kind, id). Tolerates an id
/// that itself contains a colon by splitting only on the FIRST one.
({String kind, String id}) splitDocEntityId(String entityId) {
  final i = entityId.indexOf(':');
  if (i < 0) return (kind: '', id: entityId);
  return (kind: entityId.substring(0, i), id: entityId.substring(i + 1));
}

/// A cached document: a sheet/doc carries [payloadJson] (Univer snapshot), a PDF
/// carries [bytes]. Exactly one of the two is populated. The sync fields
/// ([dirty], [deleted], [baseUpdatedAt], [lastSyncedAt]) mirror the task/note
/// cache rows and drive the offline-first sync engine.
class CachedDoc {
  const CachedDoc({
    required this.kind,
    required this.id,
    required this.name,
    this.payloadJson,
    this.bytes,
    this.updatedAt,
    this.dirty = false,
    this.deleted = false,
    this.baseUpdatedAt,
    this.lastSyncedAt,
  });

  final String kind;
  final String id;
  final String name;
  final String? payloadJson;
  final List<int>? bytes;
  final String? updatedAt;
  final bool dirty;
  final bool deleted;
  final String? baseUpdatedAt;
  final String? lastSyncedAt;

  /// Decoded Univer snapshot (sheets/docs). Empty map when not a payload doc or
  /// the stored JSON is malformed.
  Map<String, dynamic> get payload {
    final raw = payloadJson;
    if (raw == null || raw.isEmpty) return const {};
    try {
      final decoded = jsonDecode(raw);
      return decoded is Map ? Map<String, dynamic>.from(decoded) : const {};
    } catch (_) {
      return const {};
    }
  }

  static CachedDoc fromRow(Map<String, Object?> r) {
    final rawBytes = r['bytes'];
    return CachedDoc(
      kind: r['kind'] as String,
      id: r['id'] as String,
      name: (r['name'] as String?) ?? 'Untitled',
      payloadJson: r['payload'] as String?,
      bytes: rawBytes is Uint8List
          ? rawBytes
          : (rawBytes is List ? List<int>.from(rawBytes) : null),
      updatedAt: r['updated_at'] as String?,
      dirty: ((r['dirty'] as int?) ?? 0) == 1,
      deleted: ((r['deleted'] as int?) ?? 0) == 1,
      baseUpdatedAt: r['base_updated_at'] as String?,
      lastSyncedAt: r['last_synced_at'] as String?,
    );
  }
}

/// One queued document mutation awaiting push to the server.
class DocOutboxItem {
  final int seq;
  final String op;
  final String entityId; // composite "<kind>:<id>"
  final Map<String, dynamic> payload;
  final String createdAt;
  final int attempts;

  const DocOutboxItem({
    required this.seq,
    required this.op,
    required this.entityId,
    required this.payload,
    required this.createdAt,
    this.attempts = 0,
  });

  String get kind => splitDocEntityId(entityId).kind;
  String get docId => splitDocEntityId(entityId).id;

  factory DocOutboxItem.fromRow(Map<String, Object?> row) => DocOutboxItem(
        seq: row['seq'] as int,
        op: row['op'] as String,
        entityId: row['entity_id'] as String,
        payload: _decodePayload(row['payload'] as String?),
        createdAt: row['created_at'] as String? ?? '',
        attempts: (row['attempts'] as int?) ?? 0,
      );

  static Map<String, dynamic> _decodePayload(String? raw) {
    if (raw == null || raw.isEmpty) return <String, dynamic>{};
    final decoded = jsonDecode(raw);
    return decoded is Map
        ? Map<String, dynamic>.from(decoded)
        : <String, dynamic>{};
  }
}

/// A logged document conflict — written whenever last-write-wins overwrites a
/// value the loser also changed, or a create is rejected. Never silently
/// dropped. Shares the generic `conflicts` table with the other engines; the
/// `id` is the composite `<kind>:<id>` so doc conflicts don't collide with a
/// task/note that happens to share the bare id.
class DocConflictRow {
  final String id;
  final String field;
  final String? local;
  final String? server;
  final String at;

  const DocConflictRow({
    required this.id,
    required this.field,
    required this.local,
    required this.server,
    required this.at,
  });
}

/// Offline-first cache + sync source for the office suite (Sheets / Docs / PDF).
///
/// Sheets/Docs create/edit/delete locally-first (dirty row + outbox op) and
/// reconcile with the server via `document_sync.dart`. PDFs are import-only
/// (content immutable server-side) so they only ever enqueue a `delete`; their
/// metadata + tombstones still flow through `/changes` so a web/agent delete
/// propagates to mobile.
class DocumentCacheDao {
  DocumentCacheDao(this._db,
      {String Function()? now, int budgetBytes = kDocumentCacheBudgetBytes})
      : _now = now ?? (() => DateTime.now().toUtc().toIso8601String()),
        _budgetBytes = budgetBytes;

  final Database _db;
  final String Function() _now;
  final int _budgetBytes;

  static const _table = 'document_cache';
  static const _listTable = 'document_list_cache';

  // ── Reads ──────────────────────────────────────────────────────────────

  /// Read a single cached document, or null on a miss.
  Future<CachedDoc?> getDoc(String kind, String id) async {
    final r = await _rawRow(kind, id);
    return r == null ? null : CachedDoc.fromRow(r);
  }

  /// Raw cache row (including sync columns) — used by the sync engine for
  /// last-write-wins decisions. Returns null when absent.
  Future<Map<String, Object?>?> getRow(String kind, String id) =>
      _rawRowOn(_db, kind, id);

  Future<Map<String, Object?>?> _rawRow(String kind, String id) =>
      _rawRowOn(_db, kind, id);

  Future<Map<String, Object?>?> _rawRowOn(
      DatabaseExecutor exec, String kind, String id) async {
    final rows = await exec.query(_table,
        where: 'kind = ? AND id = ?', whereArgs: [kind, id], limit: 1);
    return rows.isEmpty ? null : rows.first;
  }

  /// All non-deleted cached docs for [kind], newest-cached first. Used to merge
  /// the local cache into the network list so dirty/local-only docs surface.
  Future<List<CachedDoc>> listDocs(String kind) async {
    final rows = await _db.query(_table,
        where: 'kind = ? AND deleted = 0',
        whereArgs: [kind],
        orderBy: 'cached_at DESC');
    return rows.map(CachedDoc.fromRow).toList();
  }

  /// Ids of non-deleted docs of [kind] with un-pushed local changes (dirty=1).
  Future<Set<String>> dirtyIds(String kind) async {
    final rows = await _db.query(_table,
        columns: ['id'],
        where: 'kind = ? AND dirty = 1 AND deleted = 0',
        whereArgs: [kind]);
    return rows.map((r) => r['id'] as String).toSet();
  }

  // ── Read-through cache writes (server-authoritative, CLEAN) ──────────────

  /// Upsert a SERVER-authoritative document into the cache (clean — not pending
  /// push). Clears any prior dirty/deleted state. Used after a successful
  /// network read/edit where the server already holds this exact content.
  Future<void> putServerDoc({
    required String kind,
    required String id,
    required String name,
    String? payloadJson,
    List<int>? bytes,
    String? updatedAt,
  }) async {
    final size =
        bytes?.length ?? (payloadJson == null ? 0 : utf8.encode(payloadJson).length);
    final now = _now();
    await _db.insert(
      _table,
      {
        'kind': kind,
        'id': id,
        'name': name,
        'payload': payloadJson,
        'bytes': bytes == null ? null : Uint8List.fromList(bytes),
        'updated_at': updatedAt,
        'byte_size': size,
        'cached_at': now,
        'dirty': 0,
        'deleted': 0,
        'last_synced_at': now,
        'base_updated_at': updatedAt,
      },
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
    await _evictOverBudget();
  }

  /// Backwards-compatible alias kept for any caller still using the old name.
  @Deprecated('Use putServerDoc — explicit about clean/server-authoritative.')
  Future<void> putDoc({
    required String kind,
    required String id,
    required String name,
    String? payloadJson,
    List<int>? bytes,
    String? updatedAt,
  }) =>
      putServerDoc(
          kind: kind,
          id: id,
          name: name,
          payloadJson: payloadJson,
          bytes: bytes,
          updatedAt: updatedAt);

  // ── Local-first mutations (set dirty + enqueue outbox) ───────────────────

  /// Create a sheet/doc locally. Mints a client UUID when [id] is omitted so the
  /// same id replays idempotently to the server. Returns the stored row's id.
  Future<String> applyLocalCreate({
    required String kind,
    String? id,
    required String name,
    String? payloadJson,
  }) async {
    final docId = id ?? newLocalId();
    final now = _now();
    final size = payloadJson == null ? 0 : utf8.encode(payloadJson).length;
    await _db.transaction((txn) async {
      await txn.insert(
        _table,
        {
          'kind': kind,
          'id': docId,
          'name': name,
          'payload': payloadJson,
          'bytes': null,
          'updated_at': now,
          'byte_size': size,
          'cached_at': now,
          'dirty': 1,
          'deleted': 0,
          'last_synced_at': null,
          'base_updated_at': null,
        },
        conflictAlgorithm: ConflictAlgorithm.replace,
      );
      final payload = <String, dynamic>{
        'id': docId,
        'name': name,
        'payload': ?payloadJson,
      };
      await _enqueueTxn(txn, DocOutboxOp.create, kind, docId, payload, now);
    });
    return docId;
  }

  /// Apply a local EDIT to an existing sheet/doc: write the new payload, bump
  /// updated_at + dirty, carry [baseUpdatedAt] for CAS, and enqueue an `update`.
  /// Inserts a fresh dirty row when the doc isn't cached yet (e.g. the editor
  /// opened it cold), so the edit is never lost.
  Future<void> applyLocalEdit({
    required String kind,
    required String id,
    required String name,
    required String payloadJson,
    String? baseUpdatedAt,
  }) async {
    final now = _now();
    final size = utf8.encode(payloadJson).length;
    await _db.transaction((txn) async {
      final existing = await _rawRowOn(txn, kind, id);
      if (existing == null) {
        await txn.insert(
          _table,
          {
            'kind': kind,
            'id': id,
            'name': name,
            'payload': payloadJson,
            'bytes': null,
            'updated_at': now,
            'byte_size': size,
            'cached_at': now,
            'dirty': 1,
            'deleted': 0,
            'last_synced_at': null,
            'base_updated_at': baseUpdatedAt,
          },
          conflictAlgorithm: ConflictAlgorithm.replace,
        );
      } else {
        await txn.update(
          _table,
          {
            'payload': payloadJson,
            'updated_at': now,
            'byte_size': size,
            'cached_at': now,
            'dirty': 1,
            'base_updated_at': baseUpdatedAt,
          },
          where: 'kind = ? AND id = ?',
          whereArgs: [kind, id],
        );
      }
      final payload = <String, dynamic>{
        'id': id,
        'payload': payloadJson,
        'base_updated_at': ?baseUpdatedAt,
      };
      await _enqueueTxn(txn, DocOutboxOp.update, kind, id, payload, now);
    });
  }

  /// Tombstone a document locally (deleted=1) + enqueue a `delete`. The row
  /// stays as a tombstone so the delete survives until pushed. Idempotent — a
  /// missing row simply enqueues nothing.
  Future<bool> applyLocalDelete(String kind, String id) async {
    final existing = await _rawRow(kind, id);
    if (existing == null) return false;
    final now = _now();
    await _db.transaction((txn) async {
      await txn.update(
        _table,
        {'deleted': 1, 'updated_at': now, 'dirty': 1, 'cached_at': now},
        where: 'kind = ? AND id = ?',
        whereArgs: [kind, id],
      );
      await _enqueueTxn(txn, DocOutboxOp.delete, kind, id, {'id': id}, now);
    });
    return true;
  }

  /// Mark a row clean after its mutation pushed successfully — re-bases its sync
  /// clock to the server-returned [updatedAt]. Used by the editor's direct PUT
  /// path (which saves AND clears dirty in one network round-trip).
  Future<void> markPushed({
    required String kind,
    required String id,
    String? updatedAt,
  }) =>
      _clearDirtyOn(_db, kind, id, serverUpdatedAt: updatedAt);

  Future<void> _clearDirtyOn(
    DatabaseExecutor exec,
    String kind,
    String id, {
    String? serverUpdatedAt,
    bool removeIfDeleted = true,
  }) async {
    final row = await _rawRowOn(exec, kind, id);
    if (row == null) return;
    final isDeleted = (row['deleted'] as int? ?? 0) == 1;
    if (isDeleted && removeIfDeleted) {
      await exec
          .delete(_table, where: 'kind = ? AND id = ?', whereArgs: [kind, id]);
      return;
    }
    final now = _now();
    await exec.update(
      _table,
      {
        'dirty': 0,
        'last_synced_at': now,
        'updated_at': ?serverUpdatedAt,
        'base_updated_at': ?serverUpdatedAt,
      },
      where: 'kind = ? AND id = ?',
      whereArgs: [kind, id],
    );
  }

  /// Atomically retire a pushed outbox item: delete its outbox row AND clear the
  /// local dirty flag (or hard-remove a pushed tombstone) in ONE transaction.
  /// Idempotent on replay.
  Future<void> commitPush(int seq, String entityId, {String? serverUpdatedAt}) async {
    final parts = splitDocEntityId(entityId);
    await _db.transaction((txn) async {
      await txn.delete('outbox', where: 'seq = ?', whereArgs: [seq]);
      await _clearDirtyOn(txn, parts.kind, parts.id,
          serverUpdatedAt: serverUpdatedAt);
    });
  }

  // ── Server-driven upserts (pull) ─────────────────────────────────────────

  /// Write a server-authoritative document into the cache, clearing local dirty
  /// state. Used after last-write-wins decides the server copy should win.
  Future<void> upsertFromServer(
    DatabaseExecutor exec, {
    required String kind,
    required String id,
    required String name,
    String? payloadJson,
    String? serverUpdatedAt,
    String? syncedAt,
  }) async {
    final now = syncedAt ?? _now();
    final size = payloadJson == null ? 0 : utf8.encode(payloadJson).length;
    // Preserve any existing cached bytes/payload we aren't replacing (PDF
    // metadata-only pulls carry no payload; don't blow away cached PDF bytes).
    final existing = await _rawRowOn(exec, kind, id);
    final existingBytes = existing == null ? null : existing['bytes'];
    final existingPayload =
        existing == null ? null : existing['payload'] as String?;
    final existingSize =
        existing == null ? 0 : ((existing['byte_size'] as int?) ?? 0);
    final Object? keepBytes = payloadJson == null ? existingBytes : null;
    final keepPayload = payloadJson ?? existingPayload;
    final int keepSize = payloadJson == null ? existingSize : size;
    await exec.insert(
      _table,
      {
        'kind': kind,
        'id': id,
        'name': name,
        'payload': keepPayload,
        'bytes': keepBytes,
        'updated_at': serverUpdatedAt,
        'byte_size': keepSize,
        'cached_at': now,
        'dirty': 0,
        'deleted': 0,
        'last_synced_at': now,
        'base_updated_at': serverUpdatedAt,
      },
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  /// Apply a server tombstone: mark the local row deleted + clear dirty. The row
  /// is kept as a tombstone (so the list filter hides it) but its payload/bytes
  /// are dropped to reclaim space. Idempotent if absent.
  Future<void> applyServerDelete(
    DatabaseExecutor exec,
    String kind,
    String id, {
    String? syncedAt,
  }) async {
    final now = syncedAt ?? _now();
    await exec.update(
      _table,
      {
        'deleted': 1,
        'dirty': 0,
        'payload': null,
        'bytes': null,
        'byte_size': 0,
        'updated_at': now,
        'last_synced_at': now,
      },
      where: 'kind = ? AND id = ?',
      whereArgs: [kind, id],
    );
  }

  // ── Transactions ─────────────────────────────────────────────────────────

  /// Run [action] inside a single DB transaction so a read + the dependent
  /// write commit atomically (the dirty-check-then-upsert race + the
  /// server-tombstone-vs-local reconcile).
  Future<T> runInTransaction<T>(Future<T> Function(DocTxn) action) {
    return _db.transaction((txn) => action(DocTxn._(this, txn)));
  }

  // ── Outbox ───────────────────────────────────────────────────────────────

  /// All queued document mutations in replay (seq ASC) order. Scoped to the
  /// document entity so a shared outbox never mixes in task/note/budget rows.
  Future<List<DocOutboxItem>> readOutbox() async {
    final rows = await _db.query('outbox',
        where: 'entity = ?', whereArgs: [kDocEntity], orderBy: 'seq ASC');
    return rows.map(DocOutboxItem.fromRow).toList();
  }

  Future<void> deleteOutboxItem(int seq) async {
    await _db.delete('outbox', where: 'seq = ?', whereArgs: [seq]);
  }

  /// Record one more failed attempt for a retryable (5xx) error. Returns count.
  Future<int> bumpOutboxAttempts(int seq) async {
    return _db.transaction<int>((txn) async {
      await txn.rawUpdate(
          'UPDATE outbox SET attempts = attempts + 1 WHERE seq = ?', [seq]);
      final rows = await txn.query('outbox',
          columns: ['attempts'], where: 'seq = ?', whereArgs: [seq], limit: 1);
      if (rows.isEmpty) return 0;
      return (rows.first['attempts'] as int?) ?? 0;
    });
  }

  /// Drop a poison outbox item that exceeded the retry budget.
  Future<void> deadLetterOutboxItem(int seq) async {
    await _db.delete('outbox', where: 'seq = ?', whereArgs: [seq]);
  }

  /// Remove every queued outbox op for a (kind, id). Used when a server
  /// tombstone makes a pending local op moot.
  Future<int> deleteOutboxForEntity(String kind, String id) {
    return _db.delete('outbox',
        where: 'entity = ? AND entity_id = ?',
        whereArgs: [kDocEntity, docEntityId(kind, id)]);
  }

  Future<int> outboxCount() async {
    final rows = await _db.rawQuery(
        'SELECT COUNT(*) AS c FROM outbox WHERE entity = ?', [kDocEntity]);
    return (rows.first['c'] as int?) ?? 0;
  }

  // ── Cursor (one shared cursor for all document kinds, suffixed by kind) ──

  Future<String?> getCursor(String kind) async {
    final rows = await _db.query('sync_state',
        where: 'entity = ?', whereArgs: ['$kDocEntity:$kind'], limit: 1);
    if (rows.isEmpty) return null;
    return rows.first['cursor'] as String?;
  }

  Future<void> setCursor(String kind, String? cursor) async {
    await _db.insert('sync_state', {'entity': '$kDocEntity:$kind', 'cursor': cursor},
        conflictAlgorithm: ConflictAlgorithm.replace);
  }

  // ── Conflicts ────────────────────────────────────────────────────────────

  Future<void> logConflict({
    required String kind,
    required String id,
    required String field,
    String? local,
    String? server,
    String? at,
  }) =>
      _logConflictOn(_db,
          kind: kind, id: id, field: field, local: local, server: server, at: at);

  Future<void> _logConflictOn(
    DatabaseExecutor exec, {
    required String kind,
    required String id,
    required String field,
    String? local,
    String? server,
    String? at,
  }) async {
    final cid = docEntityId(kind, id);
    final where = StringBuffer('id = ? AND field = ?');
    final args = <Object>[cid, field];
    if (local == null) {
      where.write(' AND local IS NULL');
    } else {
      where.write(' AND local = ?');
      args.add(local);
    }
    if (server == null) {
      where.write(' AND server IS NULL');
    } else {
      where.write(' AND server = ?');
      args.add(server);
    }
    final dupes = await exec.query('conflicts',
        where: where.toString(), whereArgs: args, limit: 1);
    if (dupes.isNotEmpty) return;
    await exec.insert('conflicts', {
      'id': cid,
      'field': field,
      'local': local,
      'server': server,
      'at': at ?? _now(),
    });
  }

  /// Read document conflicts only (entity_id prefixed by a known doc kind).
  Future<List<DocConflictRow>> readConflicts() async {
    final rows = await _db.query('conflicts', orderBy: 'at ASC');
    return rows
        .map((r) => DocConflictRow(
              id: r['id'] as String? ?? '',
              field: r['field'] as String? ?? '',
              local: r['local'] as String?,
              server: r['server'] as String?,
              at: r['at'] as String? ?? '',
            ))
        .where((c) =>
            c.id.startsWith('sheets:') ||
            c.id.startsWith('docs:') ||
            c.id.startsWith('pdf:'))
        .toList();
  }

  // ── List index cache (the list view) ──────────────────────────────────────

  /// The cached document index for [kind] (the list view), or null on a miss.
  Future<List<Map<String, dynamic>>?> getList(String kind) async {
    final rows = await _db.query(_listTable,
        where: 'kind = ?', whereArgs: [kind], limit: 1);
    if (rows.isEmpty) return null;
    final raw = rows.first['items'] as String?;
    if (raw == null || raw.isEmpty) return null;
    try {
      final decoded = jsonDecode(raw);
      if (decoded is! List) return null;
      return decoded
          .whereType<Map>()
          .map((e) => Map<String, dynamic>.from(e))
          .toList();
    } catch (_) {
      return null;
    }
  }

  /// Cache the document index for [kind].
  Future<void> putList(String kind, List<Map<String, dynamic>> items) async {
    await _db.insert(_listTable,
        {'kind': kind, 'items': jsonEncode(items), 'cached_at': _now()},
        conflictAlgorithm: ConflictAlgorithm.replace);
  }

  /// Drop a single cached document (e.g. after the server confirms a delete).
  Future<void> deleteDoc(String kind, String id) async {
    await _db.delete(_table, where: 'kind = ? AND id = ?', whereArgs: [kind, id]);
  }

  // ── Eviction ───────────────────────────────────────────────────────────────

  /// Sum of cached payload/byte sizes — exposed for eviction + tests.
  Future<int> totalBytes() async {
    final res = await _db
        .rawQuery('SELECT COALESCE(SUM(byte_size), 0) AS t FROM $_table');
    final v = res.first['t'];
    return v is int ? v : int.tryParse('$v') ?? 0;
  }

  /// Evict least-recently-cached CLEAN rows until the cache fits the budget.
  /// Dirty/tombstoned rows are NEVER evicted — they hold un-pushed user edits.
  Future<void> _evictOverBudget() async {
    if (await totalBytes() <= _budgetBytes) return;
    final rows = await _db.query(
      _table,
      columns: ['kind', 'id', 'byte_size'],
      where: 'dirty = 0 AND deleted = 0',
      orderBy: 'cached_at ASC',
    );
    var running = await totalBytes();
    for (final r in rows) {
      if (running <= _budgetBytes) break;
      await _db.delete(_table,
          where: 'kind = ? AND id = ?', whereArgs: [r['kind'], r['id']]);
      running -= (r['byte_size'] as int?) ?? 0;
    }
  }

  // ── Internals ────────────────────────────────────────────────────────────

  Future<void> _enqueueTxn(
    Transaction txn,
    String op,
    String kind,
    String id,
    Map<String, dynamic> payload,
    String createdAt,
  ) async {
    await txn.insert('outbox', {
      'op': op,
      'entity': kDocEntity,
      'entity_id': docEntityId(kind, id),
      'payload': jsonEncode(payload),
      'created_at': createdAt,
    });
  }
}

/// Transaction-scoped façade over a [DocumentCacheDao] handed out by
/// [DocumentCacheDao.runInTransaction]. Every call runs on the SAME open
/// transaction so a read + dependent write commit atomically.
class DocTxn {
  final DocumentCacheDao _dao;
  final Transaction _txn;
  const DocTxn._(this._dao, this._txn);

  Future<Map<String, Object?>?> getRow(String kind, String id) =>
      _dao._rawRowOn(_txn, kind, id);

  Future<void> upsertFromServer({
    required String kind,
    required String id,
    required String name,
    String? payloadJson,
    String? serverUpdatedAt,
    String? syncedAt,
  }) =>
      _dao.upsertFromServer(_txn,
          kind: kind,
          id: id,
          name: name,
          payloadJson: payloadJson,
          serverUpdatedAt: serverUpdatedAt,
          syncedAt: syncedAt);

  Future<void> applyServerDelete(String kind, String id, {String? syncedAt}) =>
      _dao.applyServerDelete(_txn, kind, id, syncedAt: syncedAt);

  Future<void> logConflict({
    required String kind,
    required String id,
    required String field,
    String? local,
    String? server,
    String? at,
  }) =>
      _dao._logConflictOn(_txn,
          kind: kind, id: id, field: field, local: local, server: server, at: at);

  Future<int> deleteOutboxForEntity(String kind, String id) => _txn.delete(
        'outbox',
        where: 'entity = ? AND entity_id = ?',
        whereArgs: [kDocEntity, docEntityId(kind, id)],
      );
}
