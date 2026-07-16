import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:sqflite_sqlcipher/sqflite.dart';

import '../models/note.dart';
import 'app_db.dart';

/// Outbox operation kinds for notes. Replayed against the server in `seq` order.
/// Notes have NO `complete` op (unlike tasks) — only create / update / delete.
class NoteOutboxOp {
  static const create = 'create';
  static const update = 'update';
  static const delete = 'delete';
}

/// The entity tag stored in the generic `outbox` / `sync_state` rows so the
/// notes engine and the tasks engine can share the same tables without
/// colliding.
const String kNoteEntity = 'note';

/// One queued note mutation awaiting push to the server.
class NoteOutboxItem {
  final int seq;
  final String op;
  final String entity;
  final String entityId;
  final Map<String, dynamic> payload;
  final String createdAt;

  /// How many times pushing this item failed with a retryable server error
  /// (5xx). After [NoteSync.kMaxPushAttempts] it is dead-lettered instead of
  /// being retried forever.
  final int attempts;

  const NoteOutboxItem({
    required this.seq,
    required this.op,
    required this.entity,
    required this.entityId,
    required this.payload,
    required this.createdAt,
    this.attempts = 0,
  });

  factory NoteOutboxItem.fromRow(Map<String, Object?> row) => NoteOutboxItem(
        seq: row['seq'] as int,
        op: row['op'] as String,
        entity: row['entity'] as String,
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

/// A logged conflict — written whenever last-write-wins overwrites a value the
/// loser had also changed. Never silently dropped. (Same shape as the tasks
/// engine; both read/write the shared `conflicts` table.)
class NoteConflictRow {
  final String id;
  final String field;
  final String? local;
  final String? server;
  final String at;

  const NoteConflictRow({
    required this.id,
    required this.field,
    required this.local,
    required this.server,
    required this.at,
  });
}

/// Local persistence + queueing for notes. All methods are storage-only — no
/// network. The sync engine drains the outbox and applies server deltas.
///
/// Mirrors [TaskDao] one-for-one for the Notes domain. The notable Notes
/// specifics:
///   * `tags` is a `List<String>` in the model but a single TEXT column in the
///     cache — it is JSON-encoded on write and decoded on read.
///   * the [Note] model surfaces `updated_at`, but the authoritative sync clock
///     lives in the `note_cache.updated_at` column (stamped from the server JSON
///     on pull, stamped to `now` on local create/edit).
class NoteDao {
  final Database _db;

  /// Wall-clock source for `updated_at` / `created_at`. Injectable so tests can
  /// pin deterministic timestamps for last-write-wins assertions.
  final String Function() _now;

  NoteDao(this._db, {String Function()? now}) : _now = now ?? _defaultNowIso;

  static String _defaultNowIso() => DateTime.now().toUtc().toIso8601String();

  // ── Reads ──────────────────────────────────────────────────────────────

  /// All non-deleted notes, newest-created first (mirrors the server list).
  Future<List<Note>> list() async {
    final rows = await _db.query(
      'note_cache',
      where: 'deleted = 0',
      orderBy: 'created_at DESC, id ASC',
    );
    return rows.map(_noteFromRow).toList();
  }

  /// Ids of non-deleted notes with un-pushed local changes (dirty=1). The
  /// Notes UI shows a cloud-off badge on these.
  Future<Set<String>> dirtyIds() async {
    final rows = await _db.query(
      'note_cache',
      columns: ['id'],
      where: 'dirty = 1 AND deleted = 0',
    );
    return rows.map((r) => r['id'] as String).toSet();
  }

  Future<Note?> getById(String id) async {
    final rows = await _db.query(
      'note_cache',
      where: 'id = ?',
      whereArgs: [id],
      limit: 1,
    );
    if (rows.isEmpty) return null;
    return _noteFromRow(rows.first);
  }

  /// Raw cache row (including dirty/deleted/updated_at) — used by the sync
  /// engine for last-write-wins decisions. Returns null when absent.
  Future<Map<String, Object?>?> getRow(String id) => _getRowOn(_db, id);

  Future<Map<String, Object?>?> _getRowOn(
    DatabaseExecutor exec,
    String id,
  ) async {
    final rows = await exec.query(
      'note_cache',
      where: 'id = ?',
      whereArgs: [id],
      limit: 1,
    );
    return rows.isEmpty ? null : rows.first;
  }

  /// Run [action] inside a single DB transaction, handing it a transaction-
  /// scoped [NoteTxn] so a read + the dependent write commit atomically (no
  /// concurrent local write can slip between them). Used by the sync engine for
  /// the dirty-check-then-upsert race and the server-tombstone-vs-local
  /// reconcile.
  Future<T> runInTransaction<T>(Future<T> Function(NoteTxn) action) {
    return _db.transaction((txn) => action(NoteTxn._(this, txn)));
  }

  // ── Server-driven upserts (pull) ─────────────────────────────────────────

  /// Write a server-authoritative note into the cache, clearing local dirty
  /// state. Used after last-write-wins decides the server copy should win.
  ///
  /// [serverUpdatedAt] is the authoritative server `updated_at` for this row
  /// (falls back to the note's own updated_at, then created_at, then now) and
  /// is what subsequent last-write-wins comparisons read back.
  Future<void> upsertFromServer(
    Note note, {
    String? serverUpdatedAt,
    String? syncedAt,
  }) =>
      _upsertFromServerOn(_db, note,
          serverUpdatedAt: serverUpdatedAt, syncedAt: syncedAt);

  Future<void> _upsertFromServerOn(
    DatabaseExecutor exec,
    Note note, {
    String? serverUpdatedAt,
    String? syncedAt,
  }) async {
    final now = syncedAt ?? _now();
    final updatedAt = serverUpdatedAt ??
        (note.updatedAt.isNotEmpty
            ? note.updatedAt
            : (note.createdAt.isNotEmpty ? note.createdAt : now));
    final row = _rowFromNote(note)
      ..['updated_at'] = updatedAt
      ..['dirty'] = 0
      ..['deleted'] = 0
      ..['last_synced_at'] = now;
    await exec.insert(
      'note_cache',
      row,
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  /// Apply a server tombstone: mark the local row deleted and clear dirty (the
  /// server already knows about this delete). Idempotent if the row is absent.
  Future<void> applyServerDelete(String id, {String? syncedAt}) =>
      _applyServerDeleteOn(_db, id, syncedAt: syncedAt);

  Future<void> _applyServerDeleteOn(
    DatabaseExecutor exec,
    String id, {
    String? syncedAt,
  }) async {
    final now = syncedAt ?? _now();
    await exec.update(
      'note_cache',
      {
        'deleted': 1,
        'dirty': 0,
        'updated_at': now,
        'last_synced_at': now,
      },
      where: 'id = ?',
      whereArgs: [id],
    );
  }

  // ── Local mutations (set dirty + enqueue outbox) ─────────────────────────

  /// Create a note locally. Mints a client UUID when [id] is omitted so the
  /// same id replays idempotently to the server. Returns the stored Note.
  Future<Note> applyLocalCreate({
    required String content,
    String? id,
    String? title,
    List<String>? tags,
    int importance = 0,
    bool pinned = false,
  }) async {
    final noteId = id ?? newLocalId();
    final now = _now();
    final note = Note(
      id: noteId,
      title: title,
      content: content,
      tags: tags ?? const [],
      importance: importance,
      pinned: pinned,
      createdAt: now,
      updatedAt: now,
    );

    await _db.transaction((txn) async {
      final row = _rowFromNote(note)
        ..['updated_at'] = now
        ..['dirty'] = 1
        ..['deleted'] = 0
        ..['last_synced_at'] = null;
      await txn.insert(
        'note_cache',
        row,
        conflictAlgorithm: ConflictAlgorithm.replace,
      );
      // Server create accepts the client id + the user-facing fields.
      final payload = <String, dynamic>{
        'id': noteId,
        'content': content,
        'title': ?title,
        'tags': ?tags,
        'importance': importance,
        'pinned': pinned,
      };
      await _enqueueTxn(txn, NoteOutboxOp.create, noteId, payload, now);
    });

    return note;
  }

  /// Patch an existing note locally. Only the supplied fields change; the rest
  /// are preserved. Bumps updated_at + dirty and enqueues an `update`.
  Future<Note?> applyLocalUpdate(
    String id, {
    String? title,
    String? content,
    List<String>? tags,
    int? importance,
    bool? pinned,
  }) async {
    final existing = await getById(id);
    if (existing == null) return null;

    final now = _now();
    final updated = existing.copyWith(
      title: title,
      content: content,
      tags: tags,
      importance: importance,
      pinned: pinned,
    );

    // Column updates for the cache (snake_case, JSON-encoded tags).
    final colUpdates = <String, Object?>{};
    if (title != null) colUpdates['title'] = title;
    if (content != null) colUpdates['content'] = content;
    if (tags != null) colUpdates['tags'] = jsonEncode(tags);
    if (importance != null) colUpdates['importance'] = importance;
    if (pinned != null) colUpdates['pinned'] = pinned ? 1 : 0;

    // Server patch payload (snake_case, native types).
    final patch = <String, dynamic>{
      'title': ?title,
      'content': ?content,
      'tags': ?tags,
      'importance': ?importance,
      'pinned': ?pinned,
    };

    await _db.transaction((txn) async {
      await txn.update(
        'note_cache',
        {
          ...colUpdates,
          'updated_at': now,
          'dirty': 1,
        },
        where: 'id = ?',
        whereArgs: [id],
      );
      await _enqueueTxn(
        txn,
        NoteOutboxOp.update,
        id,
        {'id': id, ...patch},
        now,
      );
    });

    return updated.copyWith(updatedAt: now);
  }

  /// Tombstone a note locally (deleted=1) + enqueue a `delete`. The row stays
  /// in the cache as a tombstone so the delete survives until pushed.
  Future<bool> applyLocalDelete(String id) async {
    final existing = await getById(id);
    if (existing == null) return false;

    final now = _now();
    await _db.transaction((txn) async {
      await txn.update(
        'note_cache',
        {
          'deleted': 1,
          'updated_at': now,
          'dirty': 1,
        },
        where: 'id = ?',
        whereArgs: [id],
      );
      await _enqueueTxn(txn, NoteOutboxOp.delete, id, {'id': id}, now);
    });
    return true;
  }

  /// Mark a row clean after its mutation pushed successfully. For a delete that
  /// pushed, the tombstone can be hard-removed (server now owns the delete).
  ///
  /// Idempotent: a missing row, an already-clean row, and an already-removed
  /// tombstone are all safe no-ops (so a crash-retry of [commitPush] can replay
  /// without corrupting state).
  Future<void> clearDirty(String id, {bool removeIfDeleted = true}) async {
    await _clearDirtyOn(_db, id, removeIfDeleted: removeIfDeleted);
  }

  Future<void> _clearDirtyOn(
    DatabaseExecutor exec,
    String id, {
    bool removeIfDeleted = true,
  }) async {
    final rows = await exec.query(
      'note_cache',
      where: 'id = ?',
      whereArgs: [id],
      limit: 1,
    );
    if (rows.isEmpty) return;
    final row = rows.first;
    final isDeleted = (row['deleted'] as int? ?? 0) == 1;
    if (isDeleted && removeIfDeleted) {
      await exec.delete('note_cache', where: 'id = ?', whereArgs: [id]);
      return;
    }
    await exec.update(
      'note_cache',
      {'dirty': 0, 'last_synced_at': _now()},
      where: 'id = ?',
      whereArgs: [id],
    );
  }

  // ── Outbox ───────────────────────────────────────────────────────────────

  /// All queued note mutations in replay (seq ASC) order. Scoped to the note
  /// entity so a shared outbox table never mixes task + note rows.
  Future<List<NoteOutboxItem>> readOutbox() async {
    final rows = await _db.query(
      'outbox',
      where: 'entity = ?',
      whereArgs: [kNoteEntity],
      orderBy: 'seq ASC',
    );
    return rows.map(NoteOutboxItem.fromRow).toList();
  }

  Future<void> deleteOutboxItem(int seq) async {
    await _db.delete('outbox', where: 'seq = ?', whereArgs: [seq]);
  }

  /// Atomically retire a pushed item: delete its outbox row AND clear the local
  /// dirty flag (or hard-remove a pushed tombstone) in ONE transaction. Either
  /// both land or neither does, so a crash can never leave the outbox row gone
  /// while the cache row is still marked dirty (or vice-versa). Idempotent on
  /// replay — re-running with the row already gone is a no-op.
  Future<void> commitPush(int seq, String entityId) async {
    await _db.transaction((txn) async {
      await txn.delete('outbox', where: 'seq = ?', whereArgs: [seq]);
      await _clearDirtyOn(txn, entityId);
    });
  }

  /// Record one more failed attempt for a retryable (5xx) server error so the
  /// engine can dead-letter a poison item after N tries. Returns the new count.
  Future<int> bumpOutboxAttempts(int seq) async {
    return _db.transaction<int>((txn) async {
      await txn.rawUpdate(
        'UPDATE outbox SET attempts = attempts + 1 WHERE seq = ?',
        [seq],
      );
      final rows = await txn.query(
        'outbox',
        columns: ['attempts'],
        where: 'seq = ?',
        whereArgs: [seq],
        limit: 1,
      );
      if (rows.isEmpty) return 0;
      return (rows.first['attempts'] as int?) ?? 0;
    });
  }

  /// Drop a poison outbox item that exceeded the retry budget. The local cache
  /// row is left dirty so the next pull re-establishes server truth; the item
  /// itself never blocks the rest of the queue.
  Future<void> deadLetterOutboxItem(int seq) async {
    await _db.delete('outbox', where: 'seq = ?', whereArgs: [seq]);
  }

  /// Remove every queued outbox op for [entityId]. Used when a server tombstone
  /// makes a pending local op moot (the row is gone server-side, so replaying
  /// create/update/delete against it is pointless or 404s).
  Future<int> deleteOutboxForEntity(String entityId) {
    return _db.delete(
      'outbox',
      where: 'entity = ? AND entity_id = ?',
      whereArgs: [kNoteEntity, entityId],
    );
  }

  Future<int> outboxCount() async {
    final rows = await _db.rawQuery(
      'SELECT COUNT(*) AS c FROM outbox WHERE entity = ?',
      [kNoteEntity],
    );
    return (rows.first['c'] as int?) ?? 0;
  }

  // ── Cursor ───────────────────────────────────────────────────────────────

  Future<String?> getCursor({String entity = kNoteEntity}) async {
    final rows = await _db.query(
      'sync_state',
      where: 'entity = ?',
      whereArgs: [entity],
      limit: 1,
    );
    if (rows.isEmpty) return null;
    return rows.first['cursor'] as String?;
  }

  Future<void> setCursor(String? cursor, {String entity = kNoteEntity}) async {
    await _db.insert(
      'sync_state',
      {'entity': entity, 'cursor': cursor},
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  // ── Conflicts ────────────────────────────────────────────────────────────

  /// Log a conflict, deduped on (id, field, local, server). An identical
  /// unresolved conflict already on file is NOT re-inserted, so the table can't
  /// grow unbounded when the same divergence is re-observed every sync.
  Future<void> logConflict({
    required String id,
    required String field,
    String? local,
    String? server,
    String? at,
  }) =>
      _logConflictOn(_db,
          id: id, field: field, local: local, server: server, at: at);

  Future<void> _logConflictOn(
    DatabaseExecutor exec, {
    required String id,
    required String field,
    String? local,
    String? server,
    String? at,
  }) async {
    // Build the dedup WHERE dynamically: sqflite forbids binding `null` as a
    // whereArg, so a null local/server is matched with `IS NULL` instead.
    final where = StringBuffer('id = ? AND field = ?');
    final args = <Object>[id, field];
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

    final dupes = await exec.query(
      'conflicts',
      where: where.toString(),
      whereArgs: args,
      limit: 1,
    );
    if (dupes.isNotEmpty) return;
    await exec.insert('conflicts', {
      'id': id,
      'field': field,
      'local': local,
      'server': server,
      'at': at ?? _now(),
    });
  }

  Future<List<NoteConflictRow>> readConflicts() async {
    final rows = await _db.query('conflicts', orderBy: 'at ASC');
    return rows
        .map((r) => NoteConflictRow(
              id: r['id'] as String? ?? '',
              field: r['field'] as String? ?? '',
              local: r['local'] as String?,
              server: r['server'] as String?,
              at: r['at'] as String? ?? '',
            ))
        .toList();
  }

  // ── Internals ────────────────────────────────────────────────────────────

  Future<void> _enqueueTxn(
    Transaction txn,
    String op,
    String entityId,
    Map<String, dynamic> payload,
    String createdAt,
  ) async {
    await txn.insert('outbox', {
      'op': op,
      'entity': kNoteEntity,
      'entity_id': entityId,
      'payload': jsonEncode(payload),
      'created_at': createdAt,
    });
    debugPrint(
      'NoteDao: queued outbox op=$op entity=$kNoteEntity id=$entityId',
    );
  }

  Note _noteFromRow(Map<String, Object?> row) => Note(
        id: row['id'] as String? ?? '',
        title: row['title'] as String?,
        content: row['content'] as String? ?? '',
        tags: _decodeTags(row['tags'] as String?),
        importance: (row['importance'] as int?) ?? 0,
        pinned: ((row['pinned'] as int?) ?? 0) != 0,
        traceSessionId: row['trace_session_id'] as String?,
        titleKey: row['title_key'] as String?,
        createdAt: row['created_at'] as String? ?? '',
        updatedAt: row['updated_at'] as String? ?? '',
      );

  Map<String, Object?> _rowFromNote(Note n) => {
        'id': n.id,
        'title': n.title,
        'content': n.content,
        'tags': jsonEncode(n.tags),
        'importance': n.importance,
        'pinned': n.pinned ? 1 : 0,
        'trace_session_id': n.traceSessionId,
        'title_key': n.titleKey,
        'created_at': n.createdAt,
        'updated_at': n.updatedAt,
      };

  /// Decode the JSON-encoded `tags` TEXT column back into a list. Tolerates a
  /// null, an empty string, a bare JSON array, or (defensively) a legacy
  /// comma-separated string.
  static List<String> _decodeTags(String? raw) {
    if (raw == null || raw.isEmpty) return const [];
    try {
      final decoded = jsonDecode(raw);
      if (decoded is List) return decoded.map((e) => e.toString()).toList();
      return const [];
    } catch (_) {
      // Legacy / non-JSON content — fall back to a comma split.
      return raw.split(',').map((s) => s.trim()).where((s) => s.isNotEmpty).toList();
    }
  }
}

/// Transaction-scoped façade over a [NoteDao] handed out by
/// [NoteDao.runInTransaction]. Every call runs on the SAME open transaction, so
/// a read followed by a dependent write commits atomically — nothing else can
/// interleave between them.
class NoteTxn {
  final NoteDao _dao;
  final Transaction _txn;
  const NoteTxn._(this._dao, this._txn);

  /// Raw cache row for [id] on this transaction (null when absent).
  Future<Map<String, Object?>?> getRow(String id) => _dao._getRowOn(_txn, id);

  /// Write the server-authoritative copy on this transaction.
  Future<void> upsertFromServer(
    Note note, {
    String? serverUpdatedAt,
    String? syncedAt,
  }) =>
      _dao._upsertFromServerOn(_txn, note,
          serverUpdatedAt: serverUpdatedAt, syncedAt: syncedAt);

  /// Apply a server tombstone on this transaction.
  Future<void> applyServerDelete(String id, {String? syncedAt}) =>
      _dao._applyServerDeleteOn(_txn, id, syncedAt: syncedAt);

  /// Log a (deduped) conflict on this transaction.
  Future<void> logConflict({
    required String id,
    required String field,
    String? local,
    String? server,
    String? at,
  }) =>
      _dao._logConflictOn(_txn,
          id: id, field: field, local: local, server: server, at: at);

  /// Drop every queued outbox op for [entityId] on this transaction (used when
  /// a server tombstone makes pending local ops moot).
  Future<int> deleteOutboxForEntity(String entityId) => _txn.delete(
        'outbox',
        where: 'entity = ? AND entity_id = ?',
        whereArgs: [kNoteEntity, entityId],
      );
}
