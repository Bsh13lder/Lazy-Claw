import 'dart:convert';

import 'package:sqflite_sqlcipher/sqflite.dart';

import '../models/expense.dart';
import '../models/project.dart';
import 'app_db.dart';

/// Outbox operation kinds for the budgets domain. Replayed against the server in
/// `seq` order. Both projects and expenses support create/update/delete — an
/// expense can be edited in place (amount/description/vendor/project/date) via
/// the detail sheet, which enqueues an `update` op.
class BudgetsOutboxOp {
  static const create = 'create';
  static const update = 'update';
  static const delete = 'delete';
}

/// Outbox `entity` discriminators for the two budgets entities.
const String kProjectEntity = 'project';
const String kExpenseEntity = 'expense';

/// The ONE shared sync cursor for the budgets domain. A single
/// `GET /api/budgets/changes` call returns BOTH projects and expenses, so they
/// advance one cursor together (stored in `sync_state` under this entity).
const String kBudgetsCursorEntity = 'budgets';

/// One queued mutation awaiting push to the server. Shared shape across both
/// budgets entities — the `entity` field disambiguates project vs expense.
class BudgetsOutboxItem {
  final int seq;
  final String op;
  final String entity;
  final String entityId;
  final Map<String, dynamic> payload;
  final String createdAt;

  /// How many times pushing this item failed with a retryable server error
  /// (5xx). After [kMaxPushAttempts] it is dead-lettered instead of retried.
  final int attempts;

  const BudgetsOutboxItem({
    required this.seq,
    required this.op,
    required this.entity,
    required this.entityId,
    required this.payload,
    required this.createdAt,
    this.attempts = 0,
  });

  factory BudgetsOutboxItem.fromRow(Map<String, Object?> row) =>
      BudgetsOutboxItem(
        seq: row['seq'] as int,
        op: row['op'] as String,
        entity: row['entity'] as String,
        entityId: row['entity_id'] as String,
        payload: _decodePayload(row['payload'] as String?),
        createdAt: row['created_at'] as String? ?? '',
        attempts: (row['attempts'] as int?) ?? 0,
      );

  bool get isProject => entity == kProjectEntity;
  bool get isExpense => entity == kExpenseEntity;

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
class BudgetsConflictRow {
  final String id;
  final String field;
  final String? local;
  final String? server;
  final String at;

  const BudgetsConflictRow({
    required this.id,
    required this.field,
    required this.local,
    required this.server,
    required this.at,
  });
}

/// Local persistence + queueing for budgets (projects + expenses). All methods
/// are storage-only — no network. The [BudgetsSync] engine drains the outbox and
/// applies server deltas. Mirrors `TaskDao` exactly, but spans two cache tables
/// (`project_cache`, `expense_cache`) while sharing the generic
/// `outbox`/`sync_state`/`conflicts` tables.
class BudgetsDao {
  final Database _db;

  /// Wall-clock source for `updated_at` / `created_at`. Injectable so tests can
  /// pin deterministic timestamps for last-write-wins assertions.
  final String Function() _now;

  BudgetsDao(this._db, {String Function()? now}) : _now = now ?? _defaultNowIso;

  static String _defaultNowIso() => DateTime.now().toUtc().toIso8601String();

  // ── Reads: projects ───────────────────────────────────────────────────────

  /// All non-deleted projects, newest-created first (mirrors the server list).
  Future<List<Project>> listProjects() async {
    final rows = await _db.query(
      'project_cache',
      where: 'deleted = 0',
      orderBy: 'created_at DESC, id ASC',
    );
    return rows.map(_projectFromRow).toList();
  }

  /// Ids of non-deleted projects with un-pushed local changes (dirty=1).
  Future<Set<String>> dirtyProjectIds() async {
    final rows = await _db.query(
      'project_cache',
      columns: ['id'],
      where: 'dirty = 1 AND deleted = 0',
    );
    return rows.map((r) => r['id'] as String).toSet();
  }

  Future<Project?> getProject(String id) async {
    final rows = await _db.query(
      'project_cache',
      where: 'id = ?',
      whereArgs: [id],
      limit: 1,
    );
    if (rows.isEmpty) return null;
    return _projectFromRow(rows.first);
  }

  // ── Reads: expenses ───────────────────────────────────────────────────────

  /// All non-deleted, non-void expenses, newest-spent first. Falls back to
  /// created_at ordering so freshly added local rows surface at the top.
  Future<List<Expense>> listExpenses() async {
    final rows = await _db.query(
      'expense_cache',
      where: "deleted = 0 AND (status IS NULL OR status != 'void')",
      orderBy: 'created_at DESC, id ASC',
    );
    return rows.map(_expenseFromRow).toList();
  }

  /// Ids of non-deleted expenses with un-pushed local changes (dirty=1).
  Future<Set<String>> dirtyExpenseIds() async {
    final rows = await _db.query(
      'expense_cache',
      columns: ['id'],
      where: 'dirty = 1 AND deleted = 0',
    );
    return rows.map((r) => r['id'] as String).toSet();
  }

  Future<Expense?> getExpense(String id) async {
    final rows = await _db.query(
      'expense_cache',
      where: 'id = ?',
      whereArgs: [id],
      limit: 1,
    );
    if (rows.isEmpty) return null;
    return _expenseFromRow(rows.first);
  }

  // ── Raw rows (sync engine LWW) ────────────────────────────────────────────

  Future<Map<String, Object?>?> getProjectRow(String id) =>
      _getRowOn(_db, 'project_cache', id);

  Future<Map<String, Object?>?> getExpenseRow(String id) =>
      _getRowOn(_db, 'expense_cache', id);

  Future<Map<String, Object?>?> _getRowOn(
    DatabaseExecutor exec,
    String table,
    String id,
  ) async {
    final rows = await exec.query(
      table,
      where: 'id = ?',
      whereArgs: [id],
      limit: 1,
    );
    return rows.isEmpty ? null : rows.first;
  }

  /// Every NON-tombstoned expense cache row that belongs to [projectId]. Used by
  /// the project-tombstone cascade so a server project delete sweeps its local
  /// children even when the server didn't enumerate them in `deleted_expenses`.
  Future<List<Map<String, Object?>>> _childExpenseRowsOn(
    DatabaseExecutor exec,
    String projectId,
  ) async {
    return exec.query(
      'expense_cache',
      where: 'project_id = ? AND deleted = 0',
      whereArgs: [projectId],
    );
  }

  /// Run [action] inside a single DB transaction, handing it a transaction-
  /// scoped [BudgetsTxn] so a read + the dependent write commit atomically (no
  /// concurrent local write can slip between them). Used by the sync engine for
  /// the dirty-check-then-upsert race and the server-tombstone-vs-local
  /// reconcile.
  Future<T> runInTransaction<T>(Future<T> Function(BudgetsTxn) action) {
    return _db.transaction((txn) => action(BudgetsTxn._(this, txn)));
  }

  // ── Server-driven upserts (pull) ──────────────────────────────────────────

  /// Write a server-authoritative project into the cache, clearing local dirty
  /// state. [serverUpdatedAt] is the authoritative server `updated_at` and is
  /// what subsequent last-write-wins comparisons read back.
  Future<void> upsertProjectFromServer(
    Project project, {
    String? serverUpdatedAt,
    String? syncedAt,
  }) =>
      _upsertProjectOn(_db, project,
          serverUpdatedAt: serverUpdatedAt, syncedAt: syncedAt);

  Future<void> _upsertProjectOn(
    DatabaseExecutor exec,
    Project project, {
    String? serverUpdatedAt,
    String? syncedAt,
  }) async {
    final now = syncedAt ?? _now();
    final updatedAt = serverUpdatedAt ?? now;
    final row = _rowFromProject(project)
      ..['updated_at'] = updatedAt
      ..['dirty'] = 0
      ..['deleted'] = 0
      ..['last_synced_at'] = now;
    await exec.insert(
      'project_cache',
      row,
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  /// Write a server-authoritative expense into the cache, clearing local dirty
  /// state.
  Future<void> upsertExpenseFromServer(
    Expense expense, {
    String? serverUpdatedAt,
    String? syncedAt,
  }) =>
      _upsertExpenseOn(_db, expense,
          serverUpdatedAt: serverUpdatedAt, syncedAt: syncedAt);

  Future<void> _upsertExpenseOn(
    DatabaseExecutor exec,
    Expense expense, {
    String? serverUpdatedAt,
    String? syncedAt,
  }) async {
    final now = syncedAt ?? _now();
    final updatedAt = serverUpdatedAt ?? now;
    final row = _rowFromExpense(expense)
      ..['updated_at'] = updatedAt
      ..['dirty'] = 0
      ..['deleted'] = 0
      ..['last_synced_at'] = now;
    await exec.insert(
      'expense_cache',
      row,
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  /// Apply a server tombstone to a project. Idempotent if the row is absent.
  Future<void> applyServerProjectDelete(String id, {String? syncedAt}) =>
      _applyServerDeleteOn(_db, 'project_cache', id, syncedAt: syncedAt);

  /// Apply a server tombstone to an expense. Idempotent if the row is absent.
  Future<void> applyServerExpenseDelete(String id, {String? syncedAt}) =>
      _applyServerDeleteOn(_db, 'expense_cache', id, syncedAt: syncedAt);

  Future<void> _applyServerDeleteOn(
    DatabaseExecutor exec,
    String table,
    String id, {
    String? syncedAt,
  }) async {
    final now = syncedAt ?? _now();
    await exec.update(
      table,
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

  // ── Local mutations: projects ─────────────────────────────────────────────

  /// Create a project locally. Mints a client UUID when [id] is omitted so the
  /// same id replays idempotently to the server. Returns the stored Project.
  Future<Project> applyLocalProjectCreate(
    String name, {
    String? id,
    double? budget,
    String currency = 'USD',
    String? description,
  }) async {
    final projectId = id ?? newLocalId();
    final now = _now();
    final project = Project(
      id: projectId,
      name: name,
      budget: budget ?? 0.0,
      currency: currency,
      status: 'active',
      description: description,
      spent: 0.0,
      remaining: budget ?? 0.0,
    );

    await _db.transaction((txn) async {
      final row = _rowFromProject(project)
        ..['updated_at'] = now
        ..['created_at'] = now
        ..['dirty'] = 1
        ..['deleted'] = 0
        ..['last_synced_at'] = null;
      await txn.insert(
        'project_cache',
        row,
        conflictAlgorithm: ConflictAlgorithm.replace,
      );
      final payload = <String, dynamic>{
        'id': projectId,
        'name': name,
        'budget': ?budget,
        'description': ?description,
      };
      await _enqueueTxn(
          txn, BudgetsOutboxOp.create, kProjectEntity, projectId, payload, now);
    });

    // Re-read so created_at lands on the returned model.
    return (await getProject(projectId))!;
  }

  /// Patch an existing project locally (name/budget/description/status). Bumps
  /// updated_at + dirty and enqueues an `update`.
  Future<Project?> applyLocalProjectUpdate(
    String id, {
    String? name,
    double? budget,
    String? description,
    String? status,
  }) async {
    final existing = await getProject(id);
    if (existing == null) return null;

    final now = _now();
    final updated = existing.copyWith(
      name: name,
      budget: budget,
      description: description,
      status: status,
    );

    final patch = <String, dynamic>{
      'name': ?name,
      'budget': ?budget,
      'description': ?description,
      'status': ?status,
    };

    await _db.transaction((txn) async {
      await txn.update(
        'project_cache',
        {
          ...patch,
          'updated_at': now,
          'dirty': 1,
        },
        where: 'id = ?',
        whereArgs: [id],
      );
      await _enqueueTxn(
        txn,
        BudgetsOutboxOp.update,
        kProjectEntity,
        id,
        {'id': id, ...patch},
        now,
      );
    });

    return updated;
  }

  /// Tombstone a project locally (deleted=1) + enqueue a `delete`. The row stays
  /// in the cache as a tombstone so the delete survives until pushed.
  Future<bool> applyLocalProjectDelete(String id) async {
    final existing = await getProject(id);
    if (existing == null) return false;

    final now = _now();
    await _db.transaction((txn) async {
      await txn.update(
        'project_cache',
        {
          'deleted': 1,
          'updated_at': now,
          'dirty': 1,
        },
        where: 'id = ?',
        whereArgs: [id],
      );
      await _enqueueTxn(
          txn, BudgetsOutboxOp.delete, kProjectEntity, id, {'id': id}, now);
    });
    return true;
  }

  // ── Local mutations: expenses ─────────────────────────────────────────────

  /// Create an expense locally under [projectId]. Mints a client UUID when [id]
  /// is omitted so the create replays idempotently. Returns the stored Expense.
  Future<Expense> applyLocalExpenseCreate(
    String projectId,
    double amount,
    String description, {
    String? id,
    String currency = 'USD',
    String? vendor,
    String? projectName,
  }) async {
    final expenseId = id ?? newLocalId();
    final now = _now();
    final expense = Expense(
      id: expenseId,
      projectId: projectId,
      amount: amount,
      currency: currency,
      description: description,
      vendor: vendor,
      status: 'posted',
      spentAt: now,
      projectName: projectName,
    );

    await _db.transaction((txn) async {
      final row = _rowFromExpense(expense)
        ..['updated_at'] = now
        ..['created_at'] = now
        ..['dirty'] = 1
        ..['deleted'] = 0
        ..['last_synced_at'] = null;
      await txn.insert(
        'expense_cache',
        row,
        conflictAlgorithm: ConflictAlgorithm.replace,
      );
      // Server create accepts the client id + the user-facing fields. The
      // project id rides in the URL path (handled by the sync engine), not the
      // body, but we keep it in the payload so the replay knows the target.
      final payload = <String, dynamic>{
        'id': expenseId,
        'project_id': projectId,
        'amount': amount,
        'description': description,
        'vendor': ?vendor,
      };
      await _enqueueTxn(
          txn, BudgetsOutboxOp.create, kExpenseEntity, expenseId, payload, now);
    });

    return (await getExpense(expenseId))!;
  }

  /// Patch an existing expense locally (amount/description/vendor/project/notes/
  /// date). Only the supplied fields change; the rest are preserved. Bumps
  /// updated_at + dirty and enqueues an `update`. Returns the patched Expense
  /// (or null when the id is unknown). When the project changes we clear the
  /// cached `project_name` so the ledger row falls back to the live project list
  /// (which carries the correct current name) until the next pull re-stamps it.
  Future<Expense?> applyLocalExpenseUpdate(
    String id, {
    double? amount,
    String? description,
    String? vendor,
    String? projectId,
    String? notes,
    String? spentAt,
  }) async {
    final existing = await getExpense(id);
    if (existing == null) return null;

    final now = _now();

    // Column patch (snake_case) — only the supplied fields. Mirrors the server
    // PATCH body so the queued `update` op replays the same change set.
    final patch = <String, dynamic>{
      'amount': ?amount,
      'description': ?description,
      'vendor': ?vendor,
      'project_id': ?projectId,
      'notes': ?notes,
      'spent_at': ?spentAt,
    };

    await _db.transaction((txn) async {
      await txn.update(
        'expense_cache',
        {
          ...patch,
          // A project move invalidates the denormalised name cache.
          if (projectId != null) 'project_name': null,
          'updated_at': now,
          'dirty': 1,
        },
        where: 'id = ?',
        whereArgs: [id],
      );
      await _enqueueTxn(
        txn,
        BudgetsOutboxOp.update,
        kExpenseEntity,
        id,
        {'id': id, ...patch},
        now,
      );
    });

    // Re-read so the canonical row (with the cleared project_name) is returned.
    return getExpense(id);
  }

  /// Tombstone an expense locally (deleted=1) + enqueue a `delete`.
  Future<bool> applyLocalExpenseDelete(String id) async {
    final existing = await getExpense(id);
    if (existing == null) return false;

    final now = _now();
    await _db.transaction((txn) async {
      await txn.update(
        'expense_cache',
        {
          'deleted': 1,
          'updated_at': now,
          'dirty': 1,
        },
        where: 'id = ?',
        whereArgs: [id],
      );
      await _enqueueTxn(
          txn, BudgetsOutboxOp.delete, kExpenseEntity, id, {'id': id}, now);
    });
    return true;
  }

  // ── clearDirty (per entity) ───────────────────────────────────────────────

  /// Mark a row clean after its mutation pushed successfully. For a delete that
  /// pushed, the tombstone is hard-removed (server now owns the delete).
  /// Idempotent: missing/already-clean/already-removed rows are safe no-ops.
  Future<void> clearProjectDirty(String id, {bool removeIfDeleted = true}) =>
      _clearDirtyOn(_db, 'project_cache', id, removeIfDeleted: removeIfDeleted);

  Future<void> clearExpenseDirty(String id, {bool removeIfDeleted = true}) =>
      _clearDirtyOn(_db, 'expense_cache', id, removeIfDeleted: removeIfDeleted);

  Future<void> _clearDirtyOn(
    DatabaseExecutor exec,
    String table,
    String id, {
    bool removeIfDeleted = true,
  }) async {
    final rows = await exec.query(
      table,
      where: 'id = ?',
      whereArgs: [id],
      limit: 1,
    );
    if (rows.isEmpty) return;
    final row = rows.first;
    final isDeleted = (row['deleted'] as int? ?? 0) == 1;
    if (isDeleted && removeIfDeleted) {
      await exec.delete(table, where: 'id = ?', whereArgs: [id]);
      return;
    }
    await exec.update(
      table,
      {'dirty': 0, 'last_synced_at': _now()},
      where: 'id = ?',
      whereArgs: [id],
    );
  }

  /// Which cache table an outbox entity maps to.
  static String _tableForEntity(String entity) =>
      entity == kExpenseEntity ? 'expense_cache' : 'project_cache';

  // ── Outbox ─────────────────────────────────────────────────────────────────

  /// Only the budgets-domain outbox items (project + expense), in seq order.
  /// Used by the sync engine so it never touches another domain's queue rows.
  Future<List<BudgetsOutboxItem>> readBudgetsOutbox() async {
    final rows = await _db.query(
      'outbox',
      where: 'entity IN (?, ?)',
      whereArgs: [kProjectEntity, kExpenseEntity],
      orderBy: 'seq ASC',
    );
    return rows.map(BudgetsOutboxItem.fromRow).toList();
  }

  Future<void> deleteOutboxItem(int seq) async {
    await _db.delete('outbox', where: 'seq = ?', whereArgs: [seq]);
  }

  /// Atomically retire a pushed item: delete its outbox row AND clear the local
  /// dirty flag (or hard-remove a pushed tombstone) in ONE transaction. Either
  /// both land or neither does. Idempotent on replay.
  Future<void> commitPush(int seq, String entity, String entityId) async {
    await _db.transaction((txn) async {
      await txn.delete('outbox', where: 'seq = ?', whereArgs: [seq]);
      await _clearDirtyOn(txn, _tableForEntity(entity), entityId);
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
  /// row is left dirty so the next pull re-establishes server truth.
  Future<void> deadLetterOutboxItem(int seq) async {
    await _db.delete('outbox', where: 'seq = ?', whereArgs: [seq]);
  }

  /// Remove every queued outbox op for one [entity]'s [entityId]. Scoped to the
  /// given entity discriminator so a project tombstone can never wipe another
  /// domain's (or the sibling entity's) outbox row that happens to share an id.
  /// Used when a server tombstone makes a pending local op moot.
  Future<int> deleteOutboxForEntity(String entity, String entityId) async {
    return _db.delete(
      'outbox',
      where: 'entity = ? AND entity_id = ?',
      whereArgs: [entity, entityId],
    );
  }

  Future<int> outboxCount() async {
    final rows = await _db.rawQuery(
      'SELECT COUNT(*) AS c FROM outbox WHERE entity IN (?, ?)',
      [kProjectEntity, kExpenseEntity],
    );
    return (rows.first['c'] as int?) ?? 0;
  }

  // ── Cursor (shared 'budgets' entity) ──────────────────────────────────────

  Future<String?> getCursor() async {
    final rows = await _db.query(
      'sync_state',
      where: 'entity = ?',
      whereArgs: [kBudgetsCursorEntity],
      limit: 1,
    );
    if (rows.isEmpty) return null;
    return rows.first['cursor'] as String?;
  }

  Future<void> setCursor(String? cursor) async {
    await _db.insert(
      'sync_state',
      {'entity': kBudgetsCursorEntity, 'cursor': cursor},
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  // ── Conflicts ──────────────────────────────────────────────────────────────

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
    // sqflite forbids binding `null` as a whereArg, so a null local/server is
    // matched with `IS NULL` instead.
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

  Future<List<BudgetsConflictRow>> readConflicts() async {
    final rows = await _db.query('conflicts', orderBy: 'at ASC');
    return rows
        .map((r) => BudgetsConflictRow(
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
    String entity,
    String entityId,
    Map<String, dynamic> payload,
    String createdAt,
  ) async {
    await txn.insert('outbox', {
      'op': op,
      'entity': entity,
      'entity_id': entityId,
      'payload': jsonEncode(payload),
      'created_at': createdAt,
    });
  }

  Project _projectFromRow(Map<String, Object?> row) => Project(
        id: row['id'] as String? ?? '',
        name: row['name'] as String? ?? '',
        nameKey: row['name_key'] as String?,
        budget: (row['budget'] as num?)?.toDouble() ?? 0.0,
        currency: row['currency'] as String? ?? 'USD',
        status: row['status'] as String? ?? 'active',
        description: row['description'] as String?,
        lazybrainNoteId: row['lazybrain_note_id'] as String?,
        spent: (row['spent'] as num?)?.toDouble(),
        remaining: (row['remaining'] as num?)?.toDouble(),
      );

  Map<String, Object?> _rowFromProject(Project p) => {
        'id': p.id,
        'name': p.name,
        'name_key': p.nameKey,
        'budget': p.budget,
        'currency': p.currency,
        'status': p.status,
        'description': p.description,
        'lazybrain_note_id': p.lazybrainNoteId,
        'spent': p.spent,
        'remaining': p.remaining,
      };

  Expense _expenseFromRow(Map<String, Object?> row) => Expense(
        id: row['id'] as String? ?? '',
        projectId: row['project_id'] as String? ?? '',
        taskId: row['task_id'] as String?,
        amount: (row['amount'] as num?)?.toDouble() ?? 0.0,
        currency: row['currency'] as String? ?? 'USD',
        description: row['description'] as String?,
        vendor: row['vendor'] as String?,
        notes: row['notes'] as String?,
        spentAt: row['spent_at'] as String?,
        status: row['status'] as String? ?? 'posted',
        recurringExpenseId: row['recurring_expense_id'] as String?,
        lazybrainNoteId: row['lazybrain_note_id'] as String?,
        projectName: row['project_name'] as String?,
      );

  Map<String, Object?> _rowFromExpense(Expense e) => {
        'id': e.id,
        'project_id': e.projectId,
        'task_id': e.taskId,
        'amount': e.amount,
        'currency': e.currency,
        'description': e.description,
        'vendor': e.vendor,
        'notes': e.notes,
        'spent_at': e.spentAt,
        'status': e.status,
        'recurring_expense_id': e.recurringExpenseId,
        'lazybrain_note_id': e.lazybrainNoteId,
        'project_name': e.projectName,
      };
}

/// Transaction-scoped façade over a [BudgetsDao] handed out by
/// [BudgetsDao.runInTransaction]. Every call runs on the SAME open transaction,
/// so a read followed by a dependent write commits atomically.
class BudgetsTxn {
  final BudgetsDao _dao;
  final Transaction _txn;
  const BudgetsTxn._(this._dao, this._txn);

  Future<Map<String, Object?>?> getProjectRow(String id) =>
      _dao._getRowOn(_txn, 'project_cache', id);

  Future<Map<String, Object?>?> getExpenseRow(String id) =>
      _dao._getRowOn(_txn, 'expense_cache', id);

  /// Non-tombstoned expense rows belonging to [projectId] on this transaction
  /// (used by the project-tombstone cascade to sweep orphaned children).
  Future<List<Map<String, Object?>>> childExpenseRows(String projectId) =>
      _dao._childExpenseRowsOn(_txn, projectId);

  Future<void> upsertProjectFromServer(
    Project project, {
    String? serverUpdatedAt,
    String? syncedAt,
  }) =>
      _dao._upsertProjectOn(_txn, project,
          serverUpdatedAt: serverUpdatedAt, syncedAt: syncedAt);

  Future<void> upsertExpenseFromServer(
    Expense expense, {
    String? serverUpdatedAt,
    String? syncedAt,
  }) =>
      _dao._upsertExpenseOn(_txn, expense,
          serverUpdatedAt: serverUpdatedAt, syncedAt: syncedAt);

  Future<void> applyServerProjectDelete(String id, {String? syncedAt}) =>
      _dao._applyServerDeleteOn(_txn, 'project_cache', id, syncedAt: syncedAt);

  Future<void> applyServerExpenseDelete(String id, {String? syncedAt}) =>
      _dao._applyServerDeleteOn(_txn, 'expense_cache', id, syncedAt: syncedAt);

  Future<void> logConflict({
    required String id,
    required String field,
    String? local,
    String? server,
    String? at,
  }) =>
      _dao._logConflictOn(_txn,
          id: id, field: field, local: local, server: server, at: at);

  /// Drop every queued outbox op for one [entity]'s [entityId] on this
  /// transaction (scoped to the entity discriminator so a project tombstone
  /// can't wipe a sibling expense — or another domain's — outbox row).
  Future<int> deleteOutboxForEntity(String entity, String entityId) =>
      _txn.delete(
        'outbox',
        where: 'entity = ? AND entity_id = ?',
        whereArgs: [entity, entityId],
      );
}
