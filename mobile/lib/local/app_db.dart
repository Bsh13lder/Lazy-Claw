import 'dart:convert';
import 'dart:math';

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
const int kAppDbVersion = 3;

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
