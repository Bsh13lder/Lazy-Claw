import 'dart:convert';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqflite_sqlcipher/sqflite.dart';

import 'task_dao.dart' show kTaskEntity;
import 'uuid.dart';

/// Schema version for the local cache DB. Bump + add a branch in
/// [migrateAppDb] when the table shape changes.
///
/// v2: adds `outbox.attempts` so the push engine can count server 5xx retries
///     and dead-letter a poison item instead of silently dropping it.
/// v3: adds `note_cache`, `project_cache`, `expense_cache` so Notes + Budgets
///     are offline-first too (outbox/sync_state/conflicts are already generic).
/// v4: adds `project_cache.color` (a `"#RRGGBB"` hex string, nullable) so a
///     user-chosen per-project accent round-trips through the budgets sync.
/// v5: adds `project_cache.is_favorite` (INTEGER 0/1, default 0) so a pinned
///     project surfaces in the Home "Favorites" section + round-trips sync.
/// v6: adds `document_cache` + `document_list_cache` — a read-through cache for
///     the office suite (Sheets/Docs/PDF) so a document opens INSTANTLY from
///     disk while it revalidates over the network (stale-while-revalidate).
/// v7: promotes `document_cache` from a read-through cache to a SYNC SOURCE by
///     adding the same offline-first columns the task/note caches carry —
///     `dirty`, `deleted`, `last_synced_at`, `base_updated_at`. Sheets/Docs now
///     create/edit/delete locally-first (dirty row + outbox op) and reconcile
///     with the server via `document_sync.dart` (PUSH outbox → PULL /changes,
///     LWW). PDFs sync METADATA + tombstones only (content stays import-only,
///     immutable server-side) so a delete on web/agent propagates to mobile.
///     The LRU `byte_size`/`cached_at` columns are retained unchanged.
/// v8: adds `expense_cache.is_favorite` (INTEGER 0/1, default 0) so a starred
///     expense round-trips through the budgets sync — the per-expense mirror of
///     the v5 per-project favorite flag. Feeds the Money "★ Starred only" total.
/// v9: adds `budget_entry_cache` — the budget LEDGER (top-ups / "+ Add budget")
///     as an offline-first sync source, mirroring `expense_cache`. The backend
///     already serves ledger rows in `/api/budgets/changes` (`budget_entries` +
///     `deleted_budget_entries`); this table lets the Log render offline and
///     reflect cross-device top-ups, and (Phase 1B) lets a top-up be queued
///     offline as a real audit row instead of a silent budget bump.
/// v11: adds `task_cache.recur_until` (a recurring task's series end — date-only
///     `YYYY-MM-DD` or full ISO, null = repeats forever) and
///     `project_cache.start_date` + `project_cache.due_date` (a project's time
///     frame, plaintext `YYYY-MM-DD` or null). All three round-trip through the
///     offline sync; a missed mapping point would silently drop the field and
///     LWW would propagate the loss.
/// v12: adds task_cache.comments (comment-thread JSON) + the ui_prefs KV
///     table (persisted collapse/hide-completed UI state).
/// v13: one-time rewind of the 'task' sync cursor (`kTaskEntity`), mirroring
///     the v9→v10 'budgets' rewind. A cursor that ever got ahead of an
///     undelivered row orphans it forever (the server filters
///     `updated_at > since`), which is how server-minted recurring respawns
///     and agent/web edits went permanently missing from the phone while
///     local-only rows kept showing (2026-08-03 incident).
/// v14: adds `expense_cache.subtask_id` (TEXT, nullable, plaintext — mirrors
///     `task_id`) so an expense can be pinned to one checklist item of its
///     linked task, not just the task as a whole. Round-trips through the
///     budgets sync exactly like `task_id`: carried on the update outbox
///     payload only (never on create), mapped both ways in
///     `_expenseFromRow`/`_rowFromExpense`. The server enforces
///     `subtask_id != null implies task_id != null` and DEMOTES (never
///     deletes) an expense's `subtask_id` to null when its sub-task is
///     removed — the money always survives on the task.
const int kAppDbVersion = 14;

/// Secure-storage key under which the 256-bit DB passphrase is kept.
const String kDbKeyName = 'lazyclaw_db_key';

/// File name of the encrypted local database.
const String kAppDbFileName = 'lazyclaw_offline.db';

/// How long a connection waits for a competing lock to clear before giving up
/// with `SQLITE_BUSY`. The app opens this DB from TWO isolates — the foreground
/// app AND the WorkManager headless sync isolate — so concurrent writes are
/// expected; without a busy timeout the loser throws "database is locked"
/// instantly (the "database load conflict"). 5s comfortably covers a normal
/// sync transaction.
const int kBusyTimeoutMs = 5000;

/// The full DDL for every table in the offline cache. Kept in one place so the
/// real (encrypted, on-device) DB and the in-memory test DB run identical
/// schema — the sync/DAO logic is then verified against a real SQLite engine.
const List<String> kAppDbSchema = [
  '''
  CREATE TABLE IF NOT EXISTS task_cache (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    title TEXT,
    description TEXT,
    category TEXT,
    priority TEXT,
    status TEXT,
    owner TEXT,
    due_date TEXT,
    reminder_at TEXT,
    recurring TEXT,
    recur_until TEXT,
    tags TEXT,
    nag_count INTEGER,
    created_at TEXT,
    completed_at TEXT,
    last_error TEXT,
    attempt_count INTEGER,
    last_attempted_at TEXT,
    trace_session_id TEXT,
    lazybrain_note_id TEXT,
    steps TEXT,
    comments TEXT,
    allocated_budget REAL,
    updated_at TEXT,
    dirty INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    last_synced_at TEXT
  )
  ''',
  '''
  CREATE TABLE IF NOT EXISTS outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    op TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    payload TEXT,
    created_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
  )
  ''',
  '''
  CREATE TABLE IF NOT EXISTS sync_state (
    entity TEXT PRIMARY KEY,
    cursor TEXT
  )
  ''',
  '''
  CREATE TABLE IF NOT EXISTS conflicts (
    id TEXT,
    field TEXT,
    local TEXT,
    server TEXT,
    at TEXT
  )
  ''',
  '''
  CREATE TABLE IF NOT EXISTS note_cache (
    id TEXT PRIMARY KEY,
    title TEXT,
    content TEXT,
    tags TEXT,
    importance INTEGER,
    pinned INTEGER,
    trace_session_id TEXT,
    title_key TEXT,
    created_at TEXT,
    updated_at TEXT,
    dirty INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    last_synced_at TEXT
  )
  ''',
  '''
  CREATE TABLE IF NOT EXISTS project_cache (
    id TEXT PRIMARY KEY,
    name TEXT,
    name_key TEXT,
    budget REAL,
    currency TEXT,
    status TEXT,
    description TEXT,
    lazybrain_note_id TEXT,
    color TEXT,
    is_favorite INTEGER NOT NULL DEFAULT 0,
    start_date TEXT,
    due_date TEXT,
    spent REAL,
    remaining REAL,
    created_at TEXT,
    updated_at TEXT,
    dirty INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    last_synced_at TEXT
  )
  ''',
  '''
  CREATE TABLE IF NOT EXISTS expense_cache (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    task_id TEXT,
    subtask_id TEXT,
    amount REAL,
    currency TEXT,
    description TEXT,
    vendor TEXT,
    notes TEXT,
    spent_at TEXT,
    status TEXT,
    recurring_expense_id TEXT,
    lazybrain_note_id TEXT,
    project_name TEXT,
    is_favorite INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT,
    dirty INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    last_synced_at TEXT
  )
  ''',
  // Offline-first cache + sync source for the office suite. `payload` holds the
  // Univer JSON for sheets/docs; `bytes` holds raw PDF bytes. `byte_size` drives
  // LRU eviction; `cached_at` is the recency key. PK is (kind, id) because ids
  // are only unique within a kind.
  //
  // Sync columns mirror `task_cache`/`note_cache`:
  //   * dirty            — 1 when there's a local edit not yet pushed.
  //   * deleted          — 1 when locally tombstoned (kept until the delete
  //                        pushes; then hard-removed).
  //   * last_synced_at   — when this row last reconciled with the server.
  //   * base_updated_at  — the server `updated_at` this edit was based on, sent
  //                        as `base_updated_at` for optimistic-concurrency CAS.
  '''
  CREATE TABLE IF NOT EXISTS document_cache (
    kind TEXT NOT NULL,
    id TEXT NOT NULL,
    name TEXT,
    payload TEXT,
    bytes BLOB,
    updated_at TEXT,
    byte_size INTEGER NOT NULL DEFAULT 0,
    cached_at TEXT NOT NULL,
    dirty INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    last_synced_at TEXT,
    base_updated_at TEXT,
    PRIMARY KEY (kind, id)
  )
  ''',
  // Cached document index per kind (the list view), so the list paints
  // instantly while it refreshes. One row per kind; `items` is a JSON array.
  '''
  CREATE TABLE IF NOT EXISTS document_list_cache (
    kind TEXT PRIMARY KEY,
    items TEXT NOT NULL,
    cached_at TEXT NOT NULL
  )
  ''',
  // Budget LEDGER cache + sync source (top-ups / "+ Add budget"). Mirrors the
  // sync columns of `expense_cache`. `amount` is the signed delta applied to the
  // project budget; `kind` is `credit` (a sourced top-up) or `edit` (a direct-
  // set audit row). The server derives `projects.budget` from these rows, so the
  // client NEVER pushes a budget update alongside a ledger op (avoids double-
  // counting) — see budgets_sync.dart.
  '''
  CREATE TABLE IF NOT EXISTS budget_entry_cache (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    amount REAL,
    currency TEXT,
    source TEXT,
    kind TEXT,
    created_at TEXT,
    updated_at TEXT,
    dirty INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    last_synced_at TEXT
  )
  ''',
  // Tiny KV store for client-local UI state (collapse/expand, hide-completed).
  // Deliberately NOT synced — this is per-device preference, not user data.
  '''
  CREATE TABLE IF NOT EXISTS ui_prefs (
    key TEXT PRIMARY KEY,
    value TEXT
  )
  ''',
];

/// Apply the full schema to [db]. Used by both [openAppDb] (via onCreate) and
/// the test harness (which opens an in-memory ffi DB and calls this directly).
Future<void> createAppDbSchema(Database db) async {
  for (final stmt in kAppDbSchema) {
    await db.execute(stmt);
  }
}

/// Forward-migration hook. Each version bump adds a branch that runs when an
/// older on-device DB is opened, so existing user data is preserved.
Future<void> migrateAppDb(Database db, int oldVersion, int newVersion) async {
  // v1 → v2: add the per-item retry counter used by the push engine.
  if (oldVersion < 2) {
    final cols = await db.rawQuery("PRAGMA table_info('outbox')");
    final hasAttempts = cols.any((c) => c['name'] == 'attempts');
    if (!hasAttempts) {
      await db.execute(
        'ALTER TABLE outbox ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0',
      );
    }
  }
  // v2 → v3: add the Notes + Budgets offline cache tables. Every statement is
  // CREATE TABLE IF NOT EXISTS, so re-running the full schema only creates the
  // three new tables and leaves existing ones (and their data) untouched.
  if (oldVersion < 3) {
    await createAppDbSchema(db);
  }
  // v3 → v4: add the per-project color column. Idempotent — only ALTER when the
  // column is genuinely absent so re-running the migration can't throw.
  if (oldVersion < 4) {
    final cols = await db.rawQuery("PRAGMA table_info('project_cache')");
    final hasColor = cols.any((c) => c['name'] == 'color');
    if (!hasColor) {
      await db.execute('ALTER TABLE project_cache ADD COLUMN color TEXT');
    }
  }
  // v4 → v5: add the per-project favorite flag. Idempotent — only ALTER when the
  // column is genuinely absent so re-running the migration can't throw.
  if (oldVersion < 5) {
    final cols = await db.rawQuery("PRAGMA table_info('project_cache')");
    final hasFavorite = cols.any((c) => c['name'] == 'is_favorite');
    if (!hasFavorite) {
      await db.execute(
        'ALTER TABLE project_cache ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0',
      );
    }
  }
  // v5 → v6: add the document read-through cache tables. Both are
  // CREATE TABLE IF NOT EXISTS, so re-running the schema only creates the two
  // new tables and leaves existing data untouched.
  if (oldVersion < 6) {
    await createAppDbSchema(db);
  }
  // v6 → v7: promote `document_cache` to a sync source by adding the same
  // offline-first columns the task/note caches have. Idempotent — each column
  // is added only when genuinely absent, so a re-run can't throw. `createAppDbSchema`
  // is a no-op on the existing table (CREATE TABLE IF NOT EXISTS) but ensures a
  // DB that somehow skipped v6 still has both document tables before we ALTER.
  if (oldVersion < 7) {
    await createAppDbSchema(db);
    final cols = await db.rawQuery("PRAGMA table_info('document_cache')");
    final present = cols.map((c) => c['name']).toSet();
    Future<void> addCol(String name, String ddl) async {
      if (!present.contains(name)) {
        await db.execute('ALTER TABLE document_cache ADD COLUMN $ddl');
      }
    }
    await addCol('dirty', 'dirty INTEGER NOT NULL DEFAULT 0');
    await addCol('deleted', 'deleted INTEGER NOT NULL DEFAULT 0');
    await addCol('last_synced_at', 'last_synced_at TEXT');
    await addCol('base_updated_at', 'base_updated_at TEXT');
  }
  // v7 → v8: add the per-expense favorite flag (the mirror of the v5 per-project
  // one). Idempotent — only ALTER when the column is genuinely absent so a
  // re-run can't throw. Scoped to expense_cache; no other table is touched.
  if (oldVersion < 8) {
    final cols = await db.rawQuery("PRAGMA table_info('expense_cache')");
    final hasFavorite = cols.any((c) => c['name'] == 'is_favorite');
    if (!hasFavorite) {
      await db.execute(
        'ALTER TABLE expense_cache ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0',
      );
    }
  }
  // v8 → v9: add the budget ledger cache. It's a brand-new table, so the
  // idempotent `CREATE TABLE IF NOT EXISTS` in the schema is all that's needed —
  // re-running createAppDbSchema only creates what's missing and leaves every
  // existing table untouched.
  if (oldVersion < 9) {
    await createAppDbSchema(db);
  }
  // v9 → v10: rewind the shared budgets sync cursor ONCE.
  //
  // The budget ledger (`budget_entry_cache`, added in v9) shares a SINGLE
  // 'budgets' cursor (`kBudgetsCursorEntity`) with projects + expenses. A user
  // who ran the app before v9 already had that cursor advanced — by ordinary
  // project/expense pulls — PAST their existing top-ups. So after upgrading, the
  // delta pull (`fetchChanges(since=<advanced cursor>)`) returns ZERO
  // budget_entries (the server filters `updated_at > since`) and the stranded
  // project row too — leaving the per-project Log empty and the project budget
  // stale, permanently (2026-07-20 incident: 12 server top-ups never synced).
  //
  // Deleting the 'budgets' cursor makes the next `getCursor()` return null, so
  // the following sync fetches a FULL snapshot (since=null) that backfills every
  // server top-up AND the authoritative project budgets. One-time; harmless when
  // the row is absent (fresh installs never reach here).
  if (oldVersion < 10) {
    await db.delete('sync_state', where: 'entity = ?', whereArgs: ['budgets']);
  }
  // v10 → v11: add the recurring-series end date to task_cache and the project
  // time-frame columns to project_cache. Idempotent — each column is only
  // ALTERed in when genuinely absent, so a re-run can't throw (clones the v4
  // color-branch pattern).
  if (oldVersion < 11) {
    final taskCols = await db.rawQuery("PRAGMA table_info('task_cache')");
    final taskPresent = taskCols.map((c) => c['name']).toSet();
    if (!taskPresent.contains('recur_until')) {
      await db.execute('ALTER TABLE task_cache ADD COLUMN recur_until TEXT');
    }
    final projCols = await db.rawQuery("PRAGMA table_info('project_cache')");
    final projPresent = projCols.map((c) => c['name']).toSet();
    if (!projPresent.contains('start_date')) {
      await db.execute('ALTER TABLE project_cache ADD COLUMN start_date TEXT');
    }
    if (!projPresent.contains('due_date')) {
      await db.execute('ALTER TABLE project_cache ADD COLUMN due_date TEXT');
    }
  }
  // v11 → v12: add the comment-thread column to task_cache and the ui_prefs
  // KV table. Idempotent — the column is only ALTERed in when genuinely
  // absent (clones the v10→v11 pattern), and the table uses
  // CREATE TABLE IF NOT EXISTS, so a re-run can't throw.
  if (oldVersion < 12) {
    final taskCols = await db.rawQuery("PRAGMA table_info('task_cache')");
    final taskPresent = taskCols.map((c) => c['name']).toSet();
    if (!taskPresent.contains('comments')) {
      await db.execute('ALTER TABLE task_cache ADD COLUMN comments TEXT');
    }
    await db.execute(
        'CREATE TABLE IF NOT EXISTS ui_prefs (key TEXT PRIMARY KEY, value TEXT)');
  }
  // v12 → v13: rewind the 'task' sync cursor ONCE.
  //
  // The server filters tasks/changes by `updated_at > since`. A cursor that
  // ever advanced PAST a row that hadn't been delivered yet — a server-minted
  // recurring respawn, or an edit made from the web/agent side — orphans that
  // row FOREVER: every future delta pull's `since` is already past it, so it
  // can never satisfy the `>` filter again. That's exactly what happened in
  // the 2026-08-03 incident: 5 dated/recurring open tasks sat in the gap
  // between the last pull and the cursor's parked position, so
  // GET /api/tasks/changes kept answering "nothing new" while the app's
  // Calendar and widget both correctly rendered the (now dated-empty)
  // task_cache they were given.
  //
  // Deleting the 'task' cursor row (`kTaskEntity`) makes the next
  // `getCursor()` return null, so the following sync does a FULL snapshot
  // pull (since=null) that backfills every stranded row. One-time; harmless
  // when the row is absent (fresh installs never reach here). Mirrors the
  // v9→v10 'budgets' rewind — deliberately scoped to ONLY the 'task' entity,
  // leaving 'budgets' (and any other cursor) untouched.
  //
  // Guarded on the table itself existing (clones the PRAGMA-table_info guard
  // every earlier branch uses): on-device `sync_state` has existed since v1,
  // so this is a no-op check in production, but it keeps this branch from
  // throwing when an OLDER migration is exercised directly against a
  // hand-built partial schema (every migrateAppDb branch here is gated only
  // on `oldVersion`, so a direct call still runs every later branch too).
  if (oldVersion < 13) {
    final tables = await db.rawQuery(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_state'",
    );
    if (tables.isNotEmpty) {
      await db.delete('sync_state',
          where: 'entity = ?', whereArgs: [kTaskEntity]);
    }
  }
  // v13 → v14: add the sub-task expense link column. Idempotent — only
  // ALTERed in when genuinely absent, so a re-run can't throw (clones the
  // v8 is_favorite / v10→v11 column-add pattern).
  //
  // Guarded on `expense_cache` itself existing (clones the v13 branch's
  // `sync_state`-existence guard, same rationale): every branch here is
  // gated purely on `oldVersion` — `newVersion` is NOT consulted, so calling
  // `migrateAppDb(db, oldVersion, anyTarget)` with `oldVersion < 14` always
  // runs this branch too, including from an earlier migration's own test
  // exercising a hand-built PARTIAL schema (e.g. the v11 test's
  // `task_cache`/`project_cache`-only DB) that never created
  // `expense_cache` in the first place. On-device `expense_cache` has
  // existed since v3, so this is a no-op check in production.
  if (oldVersion < 14) {
    final tables = await db.rawQuery(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='expense_cache'",
    );
    if (tables.isNotEmpty) {
      final cols = await db.rawQuery("PRAGMA table_info('expense_cache')");
      final hasSubtaskId = cols.any((c) => c['name'] == 'subtask_id');
      if (!hasSubtaskId) {
        await db.execute(
            'ALTER TABLE expense_cache ADD COLUMN subtask_id TEXT');
      }
    }
  }
}

/// Per-connection PRAGMA setup applied to EVERY opened on-disk DB — in the
/// foreground app AND in the WorkManager background isolate (both route through
/// [openAppDb], so both get this). This is the core multi-isolate-safety fix:
///
/// * `busy_timeout = [kBusyTimeoutMs]` — when another connection (e.g. the
///   headless sync isolate, or the new foreground 30-min resync) holds a write
///   lock, WAIT for it to clear instead of throwing `SQLITE_BUSY` /
///   "database is locked" immediately. This is THE fix for the "database load
///   conflict" the two-isolate design otherwise hits.
/// * `journal_mode = WAL` — write-ahead logging lets a reader and a writer run
///   concurrently (readers no longer block on the writer's lock), shrinking the
///   contention window between the app and the background sync. SQLCipher fully
///   supports WAL.
/// * `foreign_keys = ON` — unchanged; enforce referential integrity.
///
/// Order matters: `busy_timeout` is set FIRST so that the `journal_mode = WAL`
/// switch (which itself needs a brief exclusive lock) can wait rather than fail
/// when another isolate is mid-write. Exposed (not inlined) so tests can apply
/// it to a plain ffi DB and assert the PRAGMAs took effect.
Future<void> configureAppDb(Database db) async {
  await db.rawQuery('PRAGMA busy_timeout = $kBusyTimeoutMs');
  await db.rawQuery('PRAGMA journal_mode = WAL');
  await db.execute('PRAGMA foreign_keys = ON');
}

/// Read the DB passphrase from secure storage, generating + persisting a fresh
/// 256-bit random key on first run. The key NEVER leaves the device keychain.
Future<String> loadOrCreateDbKey({
  FlutterSecureStorage? storage,
  Random? random,
}) async {
  final store = storage ?? const FlutterSecureStorage();
  final existing = await store.read(key: kDbKeyName);
  if (existing != null && existing.isNotEmpty) return existing;

  final key = generateDbKey(random);
  await store.write(key: kDbKeyName, value: key);
  return key;
}

/// Generate a base64 256-bit (32-byte) random passphrase for SQLCipher.
String generateDbKey([Random? random]) {
  final rng = random ?? Random.secure();
  final bytes = List<int>.generate(32, (_) => rng.nextInt(256));
  return base64Url.encode(bytes);
}

/// onCreate hook shared by every open variant: apply the full schema.
Future<void> _onCreateAppDb(Database db, int version) =>
    createAppDbSchema(db);

/// Build the open options for the encrypted app DB — the ONE place that
/// decides how a connection is opened (version, lifecycle callbacks, SQLCipher
/// [password], and [singleInstance]).
///
/// [singleInstance] (default true — sqflite's own default) MUST be false for
/// opens made from a BACKGROUND isolate (WorkManager sync, notification-action
/// handler): sqflite keys native handles by PATH when singleInstance is true,
/// so a background open of the same path returns the SAME native handle as the
/// foreground app's long-lived connection — and the background isolate's
/// `db.close()` then kills the foreground connection out from under it
/// (`DatabaseException(database_closed)` on the next foreground query).
/// With singleInstance false the caller gets a DEDICATED handle that is safe
/// (and correct) to close when done.
///
/// Returns a NEW options object on every call. Pure — unit-tested directly.
SqlCipherOpenDatabaseOptions buildAppDbOpenOptions({
  required String password,
  bool singleInstance = true,
}) {
  return SqlCipherOpenDatabaseOptions(
    version: kAppDbVersion,
    password: password,
    onConfigure: configureAppDb,
    onCreate: _onCreateAppDb,
    onUpgrade: migrateAppDb,
    singleInstance: singleInstance,
  );
}

/// Open the encrypted on-device database. The passphrase is fetched from (or
/// minted into) the platform keychain via [loadOrCreateDbKey]. Pass [pathOverride]
/// in tests/tooling; production resolves the app documents directory.
///
/// Pass `singleInstance: false` from BACKGROUND isolates so the returned
/// handle is dedicated and closing it cannot affect the foreground app's
/// connection — see [buildAppDbOpenOptions] for the full rationale.
Future<Database> openAppDb({
  FlutterSecureStorage? storage,
  String? pathOverride,
  bool singleInstance = true,
}) async {
  final key = await loadOrCreateDbKey(storage: storage);
  final dbPath = pathOverride ?? await _defaultDbPath();
  // Same call the package's top-level openDatabase makes — routed through the
  // tested options builder so every open variant shares ONE config source.
  return databaseFactory.openDatabase(
    dbPath,
    options: buildAppDbOpenOptions(password: key, singleInstance: singleInstance),
  );
}

Future<String> _defaultDbPath() async {
  final dir = await getApplicationDocumentsDirectory();
  return p.join(dir.path, kAppDbFileName);
}

/// Re-export so callers can mint ids without importing uuid.dart directly.
String newLocalId() => uuidV4();

/// Health of the opened app database.
///
/// [ok] means the encrypted on-device file DB opened normally. [degraded]
/// means every attempt to open the file DB failed and we fell back to an
/// ephemeral in-memory database so the app stays usable — the UI should show
/// a banner and offer a retry/reset.
enum DbHealthStatus { ok, degraded }

/// Immutable result describing how the database open went.
class DbHealth {
  final DbHealthStatus status;
  final Object? error;

  const DbHealth.ok()
      : status = DbHealthStatus.ok,
        error = null;
  const DbHealth.degraded(this.error) : status = DbHealthStatus.degraded;

  bool get isDegraded => status == DbHealthStatus.degraded;

  @override
  bool operator ==(Object other) =>
      other is DbHealth && other.status == status && other.error == error;

  @override
  int get hashCode => Object.hash(status, error);
}

/// Immutable pairing of an open [Database] handle with its [DbHealth].
class AppDbResult {
  final Database db;
  final DbHealth health;

  const AppDbResult(this.db, this.health);
}

/// Number of milliseconds to wait between failed file-DB open attempts.
const Duration _kFallbackBackoff = Duration(milliseconds: 150);

/// A transient lock deserves more runway than a hard failure (corruption /
/// keychain error): we must NOT wipe-degrade to an empty in-memory DB just
/// because the headless sync isolate briefly held the file. So on a lock error
/// the open is retried these many EXTRA times, with a longer backoff, before
/// ever falling back.
const int _kLockExtraRetries = 4;
const Duration _kLockBackoff = Duration(milliseconds: 400);

/// True when [e] is a transient SQLite lock/contention error ("database is
/// locked" / SQLITE_BUSY / SQLITE_LOCKED) — as opposed to genuine corruption or
/// a keychain failure. A lock is transient: the correct response is to RETRY
/// (the on-disk cache is perfectly healthy), never to discard the cache or
/// degrade to an ephemeral in-memory DB. Matched on the message text because
/// sqflite surfaces these as a `DatabaseException` whose `toString()` carries
/// the SQLite token (e.g. "database is locked (code 5 SQLITE_BUSY)"). Kept
/// deliberately specific so unrelated "...locked" errors (e.g. a locked
/// keychain) are NOT misclassified.
bool isDatabaseLockedError(Object? e) {
  if (e == null) return false;
  final msg = e.toString().toLowerCase();
  return msg.contains('database is locked') ||
      msg.contains('database table is locked') ||
      msg.contains('sqlite_busy') ||
      msg.contains('sqlite_locked');
}

/// Resilient open: retry the encrypted file DB [retries] times (short backoff
/// between tries), then fall back to an in-memory DB. ALWAYS returns a usable
/// [Database] so the provider graph can never crash on a DB-open failure.
///
/// [openImpl] and [openInMemory] are test seams. By default [openImpl] calls
/// [openAppDb] (the real encrypted file DB) and [openInMemory] opens an
/// ephemeral [inMemoryDatabasePath] DB seeded with [createAppDbSchema].
///
/// [singleInstance] is forwarded to [openAppDb] — pass false from BACKGROUND
/// isolates (see [buildAppDbOpenOptions]) so the handle is dedicated and safe
/// to close. (The in-memory fallback needs no flag: sqflite never shares
/// in-memory databases between opens.)
Future<AppDbResult> openAppDbWithFallback({
  FlutterSecureStorage? storage,
  String? pathOverride,
  int retries = 2,
  bool singleInstance = true,
  Future<Database> Function()? openImpl,
  Future<Database> Function()? openInMemory,
}) async {
  final open = openImpl ??
      () => openAppDb(
            storage: storage,
            pathOverride: pathOverride,
            singleInstance: singleInstance,
          );
  final openMem = openInMemory ?? _openInMemoryAppDb;

  Object? lastError;
  var attempt = 0;
  while (true) {
    try {
      final db = await open();
      return AppDbResult(db, const DbHealth.ok());
    } catch (err, stack) {
      lastError = err;
      // A transient lock (the other isolate held the file) gets EXTRA retries
      // with a longer backoff — degrading to an empty in-memory DB on a lock
      // would needlessly hide the user's healthy on-disk cache.
      final locked = isDatabaseLockedError(err);
      final budget = locked ? retries + _kLockExtraRetries : retries;
      // NEVER silently swallow — surface every failed attempt for diagnosis.
      debugPrint(
        'openAppDbWithFallback: file DB open attempt '
        '${attempt + 1}/${budget + 1} failed'
        '${locked ? ' (transient lock — will retry, not degrade)' : ''}: $err',
      );
      debugPrintStack(stackTrace: stack, label: 'openAppDbWithFallback');
      if (attempt < budget) {
        await Future<void>.delayed(locked ? _kLockBackoff : _kFallbackBackoff);
        attempt++;
        continue;
      }
      break;
    }
  }

  // Every file-DB attempt failed — degrade to an ephemeral in-memory DB so the
  // app keeps working (read-only-ish; nothing persists across restarts).
  debugPrint(
    'openAppDbWithFallback: exhausted ${retries + 1} attempts — '
    'falling back to in-memory DB (degraded). last error: $lastError',
  );
  final mem = await openMem();
  return AppDbResult(mem, DbHealth.degraded(lastError));
}

/// Default in-memory fallback DB: opens [inMemoryDatabasePath] and applies the
/// full schema so DAOs find their tables even in degraded mode.
Future<Database> _openInMemoryAppDb() async {
  final db = await openDatabase(inMemoryDatabasePath);
  await createAppDbSchema(db);
  return db;
}

/// Wipe the corrupt DB file + its keychain passphrase so a fresh, healthy DB
/// can be minted on the next [openAppDb] call. The caller is responsible for
/// re-opening afterwards. Best-effort: a missing file is not an error.
Future<void> resetAppDb({
  FlutterSecureStorage? storage,
  String? pathOverride,
}) async {
  final dbPath = pathOverride ?? await _defaultDbPath();
  try {
    await deleteDatabase(dbPath);
  } catch (err) {
    // File may not exist or be locked — log but don't block the key wipe.
    debugPrint('resetAppDb: deleteDatabase failed (continuing): $err');
  }
  await (storage ?? const FlutterSecureStorage()).delete(key: kDbKeyName);
}
