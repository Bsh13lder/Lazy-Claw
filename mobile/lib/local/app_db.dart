import 'dart:convert';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqflite_sqlcipher/sqflite.dart';

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
const int kAppDbVersion = 4;

/// Secure-storage key under which the 256-bit DB passphrase is kept.
const String kDbKeyName = 'lazyclaw_db_key';

/// File name of the encrypted local database.
const String kAppDbFileName = 'lazyclaw_offline.db';

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
    created_at TEXT,
    updated_at TEXT,
    dirty INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    last_synced_at TEXT
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

/// Open the encrypted on-device database. The passphrase is fetched from (or
/// minted into) the platform keychain via [loadOrCreateDbKey]. Pass [pathOverride]
/// in tests/tooling; production resolves the app documents directory.
Future<Database> openAppDb({
  FlutterSecureStorage? storage,
  String? pathOverride,
}) async {
  final key = await loadOrCreateDbKey(storage: storage);
  final dbPath = pathOverride ?? await _defaultDbPath();
  return openDatabase(
    dbPath,
    password: key,
    version: kAppDbVersion,
    onConfigure: (db) async {
      await db.execute('PRAGMA foreign_keys = ON');
    },
    onCreate: (db, version) async {
      await createAppDbSchema(db);
    },
    onUpgrade: migrateAppDb,
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

/// Resilient open: retry the encrypted file DB [retries] times (short backoff
/// between tries), then fall back to an in-memory DB. ALWAYS returns a usable
/// [Database] so the provider graph can never crash on a DB-open failure.
///
/// [openImpl] and [openInMemory] are test seams. By default [openImpl] calls
/// [openAppDb] (the real encrypted file DB) and [openInMemory] opens an
/// ephemeral [inMemoryDatabasePath] DB seeded with [createAppDbSchema].
Future<AppDbResult> openAppDbWithFallback({
  FlutterSecureStorage? storage,
  String? pathOverride,
  int retries = 2,
  Future<Database> Function()? openImpl,
  Future<Database> Function()? openInMemory,
}) async {
  final open = openImpl ??
      () => openAppDb(storage: storage, pathOverride: pathOverride);
  final openMem = openInMemory ?? _openInMemoryAppDb;

  Object? lastError;
  // attempt 0..retries inclusive => (retries + 1) total tries.
  for (var attempt = 0; attempt <= retries; attempt++) {
    try {
      final db = await open();
      return AppDbResult(db, const DbHealth.ok());
    } catch (err, stack) {
      lastError = err;
      // NEVER silently swallow — surface every failed attempt for diagnosis.
      debugPrint(
        'openAppDbWithFallback: file DB open attempt '
        '${attempt + 1}/${retries + 1} failed: $err',
      );
      debugPrintStack(stackTrace: stack, label: 'openAppDbWithFallback');
      if (attempt < retries) {
        await Future<void>.delayed(_kFallbackBackoff);
      }
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
