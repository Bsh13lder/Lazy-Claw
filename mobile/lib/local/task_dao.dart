import 'dart:convert';

import 'package:sqflite_sqlcipher/sqflite.dart';

import '../models/task.dart';
import 'app_db.dart';

/// Outbox operation kinds. Replayed against the server in `seq` order.
class OutboxOp {
  static const create = 'create';
  static const update = 'update';
  static const complete = 'complete';
  static const delete = 'delete';
}

const String kTaskEntity = 'task';

/// One queued mutation awaiting push to the server.
class OutboxItem {
  final int seq;
  final String op;
  final String entity;
  final String entityId;
  final Map<String, dynamic> payload;
  final String createdAt;

  /// How many times pushing this item failed with a retryable server error
  /// (5xx). After [TaskSync.kMaxPushAttempts] it is dead-lettered instead of
  /// being retried forever.
  final int attempts;

  const OutboxItem({
    required this.seq,
    required this.op,
    required this.entity,
    required this.entityId,
    required this.payload,
    required this.createdAt,
    this.attempts = 0,
  });

  factory OutboxItem.fromRow(Map<String, Object?> row) => OutboxItem(
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
/// loser had also changed. Never silently dropped.
class ConflictRow {
  final String id;
  final String field;
  final String? local;
  final String? server;
  final String at;

  const ConflictRow({
    required this.id,
    required this.field,
    required this.local,
    required this.server,
    required this.at,
  });
}

/// Local persistence + queueing for tasks. All methods are storage-only — no
/// network. The sync engine drains the outbox and applies server deltas.
class TaskDao {
  final Database _db;

  /// Wall-clock source for `updated_at` / `created_at`. Injectable so tests can
  /// pin deterministic timestamps for last-write-wins assertions.
  final String Function() _now;

  TaskDao(this._db, {String Function()? now})
      : _now = now ?? _defaultNowIso;

  static String _defaultNowIso() => DateTime.now().toUtc().toIso8601String();

  // ── Reads ──────────────────────────────────────────────────────────────

  /// All non-deleted tasks, newest-created first (mirrors the server list).
  Future<List<Task>> list() async {
    final rows = await _db.query(
      'task_cache',
      where: 'deleted = 0',
      orderBy: 'created_at DESC, id ASC',
    );
    return rows.map(_taskFromRow).toList();
  }

  /// Ids of non-deleted tasks with un-pushed local changes (dirty=1). The
  /// Tasks UI shows a cloud-off badge on these.
  Future<Set<String>> dirtyIds() async {
    final rows = await _db.query(
      'task_cache',
      columns: ['id'],
      where: 'dirty = 1 AND deleted = 0',
    );
    return rows.map((r) => r['id'] as String).toSet();
  }

  Future<Task?> getById(String id) async {
    final rows = await _db.query(
      'task_cache',
      where: 'id = ?',
      whereArgs: [id],
      limit: 1,
    );
    if (rows.isEmpty) return null;
    return _taskFromRow(rows.first);
  }

  /// Raw cache row (including dirty/deleted/updated_at) — used by the sync
  /// engine for last-write-wins decisions. Returns null when absent.
  Future<Map<String, Object?>?> getRow(String id) => _getRowOn(_db, id);

  Future<Map<String, Object?>?> _getRowOn(
    DatabaseExecutor exec,
    String id,
  ) async {
    final rows = await exec.query(
      'task_cache',
      where: 'id = ?',
      whereArgs: [id],
      limit: 1,
    );
    return rows.isEmpty ? null : rows.first;
  }

  /// Run [action] inside a single DB transaction, handing it a transaction-
  /// scoped [TaskTxn] so a read + the dependent write commit atomically (no
  /// concurrent local write can slip between them). Used by the sync engine for
  /// the dirty-check-then-upsert race (H2) and the server-tombstone-vs-local
  /// reconcile (H1).
  Future<T> runInTransaction<T>(Future<T> Function(TaskTxn) action) {
    return _db.transaction((txn) => action(TaskTxn._(this, txn)));
  }

  // ── Server-driven upserts (pull) ─────────────────────────────────────────

  /// Write a server-authoritative task into the cache, clearing local dirty
  /// state. Used after last-write-wins decides the server copy should win.
  ///
  /// [serverUpdatedAt] is the authoritative server `updated_at` for this row
  /// (falls back to the task's created_at, then now) and is what subsequent
  /// last-write-wins comparisons read back.
  Future<void> upsertFromServer(
    Task task, {
    String? serverUpdatedAt,
    String? syncedAt,
  }) =>
      _upsertFromServerOn(_db, task,
          serverUpdatedAt: serverUpdatedAt, syncedAt: syncedAt);

  Future<void> _upsertFromServerOn(
    DatabaseExecutor exec,
    Task task, {
    String? serverUpdatedAt,
    String? syncedAt,
  }) async {
    final now = syncedAt ?? _now();
    final updatedAt = serverUpdatedAt ??
        (task.createdAt.isNotEmpty ? task.createdAt : now);
    final row = _rowFromTask(task)
      ..['updated_at'] = updatedAt
      ..['dirty'] = 0
      ..['deleted'] = 0
      ..['last_synced_at'] = now;
    await exec.insert(
      'task_cache',
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
      'task_cache',
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

  /// Create a task locally. Mints a client UUID when [id] is omitted so the
  /// same id replays idempotently to the server. Returns the stored Task.
  Future<Task> applyLocalCreate(
    String title, {
    String? id,
    String? description,
    String? category,
    String priority = 'medium',
    String? dueDate,
    String? reminderAt,
    String? recurring,
    String owner = 'user',
    String? userId,
  }) async {
    final taskId = id ?? newLocalId();
    final now = _now();
    final task = Task(
      id: taskId,
      userId: userId ?? '',
      title: title,
      description: description,
      category: category,
      priority: priority,
      status: 'todo',
      owner: owner,
      dueDate: dueDate,
      reminderAt: reminderAt,
      recurring: recurring,
      nagCount: 0,
      createdAt: now,
    );

    await _db.transaction((txn) async {
      final row = _rowFromTask(task)
        ..['updated_at'] = now
        ..['dirty'] = 1
        ..['deleted'] = 0
        ..['last_synced_at'] = null;
      await txn.insert(
        'task_cache',
        row,
        conflictAlgorithm: ConflictAlgorithm.replace,
      );
      // Server create accepts the client id + the user-facing fields.
      final payload = <String, dynamic>{
        'id': taskId,
        'title': title,
        'description': ?description,
        'category': ?category,
        'priority': priority,
        'due_date': ?dueDate,
        'reminder_at': ?reminderAt,
        'recurring': ?recurring,
      };
      await _enqueueTxn(txn, OutboxOp.create, taskId, payload, now);
    });

    return task;
  }

  /// Patch an existing task locally. Only the supplied fields change; the rest
  /// are preserved. Bumps updated_at + dirty and enqueues an `update`.
  Future<Task?> applyLocalUpdate(
    String id, {
    String? title,
    String? description,
    String? category,
    String? priority,
    String? status,
    String? dueDate,
    String? reminderAt,
  }) async {
    final existing = await getById(id);
    if (existing == null) return null;

    final now = _now();
    final updated = existing.copyWith(
      title: title,
      description: description,
      category: category,
      priority: priority,
      status: status,
      dueDate: dueDate,
      reminderAt: reminderAt,
    );

    final patch = <String, dynamic>{
      'title': ?title,
      'description': ?description,
      'category': ?category,
      'priority': ?priority,
      'status': ?status,
      'due_date': ?dueDate,
      'reminder_at': ?reminderAt,
    };

    await _db.transaction((txn) async {
      await txn.update(
        'task_cache',
        {
          ..._fieldUpdates(patch),
          'updated_at': now,
          'dirty': 1,
        },
        where: 'id = ?',
        whereArgs: [id],
      );
      await _enqueueTxn(
        txn,
        OutboxOp.update,
        id,
        {'id': id, ...patch},
        now,
      );
    });

    return updated;
  }

  /// Mark a task complete locally (status=done) + enqueue a `complete`.
  Future<Task?> applyLocalComplete(String id) async {
    final existing = await getById(id);
    if (existing == null) return null;

    final now = _now();
    await _db.transaction((txn) async {
      await txn.update(
        'task_cache',
        {
          'status': 'done',
          'completed_at': now,
          'updated_at': now,
          'dirty': 1,
        },
        where: 'id = ?',
        whereArgs: [id],
      );
      await _enqueueTxn(txn, OutboxOp.complete, id, {'id': id}, now);
    });

    return existing.copyWith(
      status: 'done',
      completedAt: now,
    );
  }

  /// Tombstone a task locally (deleted=1) + enqueue a `delete`. The row stays
  /// in the cache as a tombstone so the delete survives until pushed.
  Future<bool> applyLocalDelete(String id) async {
    final existing = await getById(id);
    if (existing == null) return false;

    final now = _now();
    await _db.transaction((txn) async {
      await txn.update(
        'task_cache',
        {
          'deleted': 1,
          'updated_at': now,
          'dirty': 1,
        },
        where: 'id = ?',
        whereArgs: [id],
      );
      await _enqueueTxn(txn, OutboxOp.delete, id, {'id': id}, now);
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
      'task_cache',
      where: 'id = ?',
      whereArgs: [id],
      limit: 1,
    );
    if (rows.isEmpty) return;
    final row = rows.first;
    final isDeleted = (row['deleted'] as int? ?? 0) == 1;
    if (isDeleted && removeIfDeleted) {
      await exec.delete('task_cache', where: 'id = ?', whereArgs: [id]);
      return;
    }
    await exec.update(
      'task_cache',
      {'dirty': 0, 'last_synced_at': _now()},
      where: 'id = ?',
      whereArgs: [id],
    );
  }

  // ── Outbox ───────────────────────────────────────────────────────────────

  /// All queued mutations in replay (seq ASC) order.
  Future<List<OutboxItem>> readOutbox() async {
    final rows = await _db.query('outbox', orderBy: 'seq ASC');
    return rows.map(OutboxItem.fromRow).toList();
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
  /// create/update/complete/delete against it is pointless or 404s).
  Future<int> deleteOutboxForEntity(String entityId) async {
    return _db.delete(
      'outbox',
      where: 'entity_id = ?',
      whereArgs: [entityId],
    );
  }

  Future<int> outboxCount() async {
    final rows = await _db.rawQuery('SELECT COUNT(*) AS c FROM outbox');
    return (rows.first['c'] as int?) ?? 0;
  }

  // ── Cursor ───────────────────────────────────────────────────────────────

  Future<String?> getCursor({String entity = kTaskEntity}) async {
    final rows = await _db.query(
      'sync_state',
      where: 'entity = ?',
      whereArgs: [entity],
      limit: 1,
    );
    if (rows.isEmpty) return null;
    return rows.first['cursor'] as String?;
  }

  Future<void> setCursor(String? cursor, {String entity = kTaskEntity}) async {
    await _db.insert(
      'sync_state',
      {'entity': entity, 'cursor': cursor},
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  // ── Conflicts ────────────────────────────────────────────────────────────

  /// Log a conflict, deduped on (id, field, local, server). An identical
  /// unresolved conflict already on file is NOT re-inserted, so the table can't
  /// grow unbounded when the same divergence is re-observed every sync (M1).
  Future<void> logConflict({
    required String id,
    required String field,
    String? local,
    String? server,
    String? at,
  }) =>
      _logConflictOn(_db, id: id, field: field, local: local, server: server, at: at);

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

  Future<List<ConflictRow>> readConflicts() async {
    final rows = await _db.query('conflicts', orderBy: 'at ASC');
    return rows
        .map((r) => ConflictRow(
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
      'entity': kTaskEntity,
      'entity_id': entityId,
      'payload': jsonEncode(payload),
      'created_at': createdAt,
    });
  }

  /// Translate a server field-patch (snake_case keys) into column updates.
  Map<String, Object?> _fieldUpdates(Map<String, dynamic> patch) {
    final out = <String, Object?>{};
    for (final entry in patch.entries) {
      // patch keys are already column names (snake_case) for tasks.
      out[entry.key] = entry.value;
    }
    return out;
  }

  Task _taskFromRow(Map<String, Object?> row) => Task(
        id: row['id'] as String? ?? '',
        userId: row['user_id'] as String? ?? '',
        title: row['title'] as String? ?? '',
        description: row['description'] as String?,
        category: row['category'] as String?,
        priority: row['priority'] as String? ?? 'medium',
        status: row['status'] as String? ?? 'todo',
        owner: row['owner'] as String? ?? 'user',
        dueDate: row['due_date'] as String?,
        reminderAt: row['reminder_at'] as String?,
        recurring: row['recurring'] as String?,
        tags: row['tags'] as String?,
        nagCount: (row['nag_count'] as int?) ?? 0,
        createdAt: row['created_at'] as String? ?? '',
        completedAt: row['completed_at'] as String?,
        lastError: row['last_error'] as String?,
        attemptCount: row['attempt_count'] as int?,
        lastAttemptedAt: row['last_attempted_at'] as String?,
        traceSessionId: row['trace_session_id'] as String?,
        lazybrainNoteId: row['lazybrain_note_id'] as String?,
        steps: row['steps'] as String?,
        allocatedBudget: (row['allocated_budget'] as num?)?.toDouble(),
      );

  Map<String, Object?> _rowFromTask(Task t) => {
        'id': t.id,
        'user_id': t.userId,
        'title': t.title,
        'description': t.description,
        'category': t.category,
        'priority': t.priority,
        'status': t.status,
        'owner': t.owner,
        'due_date': t.dueDate,
        'reminder_at': t.reminderAt,
        'recurring': t.recurring,
        'tags': t.tags,
        'nag_count': t.nagCount,
        'created_at': t.createdAt,
        'completed_at': t.completedAt,
        'last_error': t.lastError,
        'attempt_count': t.attemptCount,
        'last_attempted_at': t.lastAttemptedAt,
        'trace_session_id': t.traceSessionId,
        'lazybrain_note_id': t.lazybrainNoteId,
        'steps': t.steps,
        'allocated_budget': t.allocatedBudget,
      };
}

/// Transaction-scoped façade over a [TaskDao] handed out by
/// [TaskDao.runInTransaction]. Every call runs on the SAME open transaction, so
/// a read followed by a dependent write commits atomically — nothing else can
/// interleave between them.
class TaskTxn {
  final TaskDao _dao;
  final Transaction _txn;
  const TaskTxn._(this._dao, this._txn);

  /// Raw cache row for [id] on this transaction (null when absent).
  Future<Map<String, Object?>?> getRow(String id) =>
      _dao._getRowOn(_txn, id);

  /// Write the server-authoritative copy on this transaction.
  Future<void> upsertFromServer(
    Task task, {
    String? serverUpdatedAt,
    String? syncedAt,
  }) =>
      _dao._upsertFromServerOn(_txn, task,
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
        where: 'entity_id = ?',
        whereArgs: [entityId],
      );
}
