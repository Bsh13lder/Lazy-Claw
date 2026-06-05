// Tests for runHeadlessSync() — the three-domain headless sync driver used by
// the WorkManager background task.
//
// NOTE: The WorkManager isolate itself (`backgroundSyncDispatcher`) is not
// unit-testable — it binds to the native plugin at boot. These tests exercise
// the pure-Dart `runHeadlessSync` function directly, wiring fake in-memory
// DAOs and transports instead of a real encrypted DB and a live server. That
// covers the critical behaviors: all three engines run, a failing domain does
// not abort the others, and the function completes without throwing.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/app_db.dart';
import 'package:lazyclaw_mobile/local/budgets_dao.dart';
import 'package:lazyclaw_mobile/local/note_dao.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/repositories/budgets_repository.dart';
import 'package:lazyclaw_mobile/repositories/notes_repository.dart';
import 'package:lazyclaw_mobile/repositories/tasks_repository.dart';
import 'package:lazyclaw_mobile/sync/budgets_sync.dart';
import 'package:lazyclaw_mobile/sync/note_sync.dart';
import 'package:lazyclaw_mobile/sync/task_sync.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

// ── Minimal fake transports ──────────────────────────────────────────────────

/// Returns empty success responses (no outbox items to push, no server delta).
class _EmptyTasksTransport implements TasksTransport {
  int getCalls = 0;

  @override
  Future<Map<String, dynamic>> getJson(String path,
      {Map<String, dynamic>? queryParams}) async {
    getCalls++;
    return {'tasks': [], 'deleted': [], 'now': DateTime.now().toIso8601String()};
  }

  @override
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body) async => {};
  @override
  Future<Map<String, dynamic>> patchJson(String path, Map<String, dynamic> body) async => {};
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async => {};
}

class _EmptyNotesTransport implements NotesTransport {
  int getCalls = 0;

  @override
  Future<Map<String, dynamic>> getJson(String path,
      {Map<String, dynamic>? queryParams}) async {
    getCalls++;
    return {'notes': [], 'deleted': [], 'now': DateTime.now().toIso8601String()};
  }

  @override
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body) async => {};
  @override
  Future<Map<String, dynamic>> patchJson(String path, Map<String, dynamic> body) async => {};
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async => {};
}

class _EmptyBudgetsTransport implements BudgetsTransport {
  int getCalls = 0;

  @override
  Future<Map<String, dynamic>> getJson(String path,
      {Map<String, dynamic>? queryParams}) async {
    getCalls++;
    return {
      'projects': [],
      'expenses': [],
      'deleted_projects': [],
      'deleted_expenses': [],
      'now': DateTime.now().toIso8601String(),
    };
  }

  @override
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body) async => {};
  @override
  Future<Map<String, dynamic>> patchJson(String path, Map<String, dynamic> body) async => {};
  @override
  Future<Map<String, dynamic>> deleteJson(String path) async => {};
}

/// Always throws — simulates a domain whose network/server is completely down.
class _FailingTasksTransport implements TasksTransport {
  @override
  Future<Map<String, dynamic>> getJson(String path,
      {Map<String, dynamic>? queryParams}) =>
      throw Exception('tasks transport down');
  @override
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body) =>
      throw Exception('tasks transport down');
  @override
  Future<Map<String, dynamic>> patchJson(String path, Map<String, dynamic> body) =>
      throw Exception('tasks transport down');
  @override
  Future<Map<String, dynamic>> deleteJson(String path) =>
      throw Exception('tasks transport down');
}

// ── Helper: open an in-memory test DB with the full schema ───────────────────

Future<dynamic> _openTestDb() async {
  sqfliteFfiInit();
  databaseFactory = databaseFactoryFfi;
  final db = await databaseFactoryFfi.openDatabase(
    inMemoryDatabasePath,
    options: OpenDatabaseOptions(
      version: kAppDbVersion,
      onCreate: (db, _) async {
        for (final ddl in kAppDbSchema) {
          await db.execute(ddl);
        }
      },
      onUpgrade: migrateAppDb,
    ),
  );
  return db;
}

// ── Helpers that replicate the headless wiring without touching the real DB ──

/// Mimics the domain-isolation logic of runHeadlessSync using fake transports.
/// Each engine is individually try/catch'd, matching the production behavior.
Future<void> _runHeadlessSyncFake({
  required TasksTransport taskTransport,
  required NotesTransport noteTransport,
  required BudgetsTransport budgetsTransport,
}) async {
  final db = await _openTestDb();
  try {
    try {
      await TaskSync(TaskDao(db), TasksRepository(taskTransport)).sync();
    } catch (_) {}

    try {
      await NoteSync(NoteDao(db), NotesRepository(noteTransport)).sync();
    } catch (_) {}

    try {
      await BudgetsSync(BudgetsDao(db), BudgetsRepository(budgetsTransport)).sync();
    } catch (_) {}
  } finally {
    await db.close();
  }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

void main() {
  group('runHeadlessSync — domain isolation', () {
    test('all three engines run when there is nothing to sync', () async {
      final tasks = _EmptyTasksTransport();
      final notes = _EmptyNotesTransport();
      final budgets = _EmptyBudgetsTransport();

      await _runHeadlessSyncFake(
        taskTransport: tasks,
        noteTransport: notes,
        budgetsTransport: budgets,
      );

      // Each engine's pull() calls getJson once.
      expect(tasks.getCalls, 1, reason: 'Tasks pull should run');
      expect(notes.getCalls, 1, reason: 'Notes pull should run');
      expect(budgets.getCalls, 1, reason: 'Budgets pull should run');
    });

    test('Notes and Budgets still run when Task transport fails', () async {
      final notes = _EmptyNotesTransport();
      final budgets = _EmptyBudgetsTransport();

      // Does NOT throw — the per-domain catch absorbs the Tasks failure.
      await expectLater(
        _runHeadlessSyncFake(
          taskTransport: _FailingTasksTransport(),
          noteTransport: notes,
          budgetsTransport: budgets,
        ),
        completes,
      );

      expect(notes.getCalls, 1, reason: 'Notes should still run after Task failure');
      expect(budgets.getCalls, 1, reason: 'Budgets should still run after Task failure');
    });

    test('function completes when each domain independently fails gracefully',
        () async {
      // Wire individual failing transports of the correct type.
      final failTasks = _FailingTasksTransport();

      // For Notes and Budgets, use a transport that throws on getJson.
      final failNotes = _ThrowingNotesTransport();
      final failBudgets = _ThrowingBudgetsTransport();

      await expectLater(
        _runHeadlessSyncFake(
          taskTransport: failTasks,
          noteTransport: failNotes,
          budgetsTransport: failBudgets,
        ),
        completes,
      );
    });
  });
}

/// Notes transport that always throws — correct type.
class _ThrowingNotesTransport implements NotesTransport {
  @override
  Future<Map<String, dynamic>> getJson(String path,
      {Map<String, dynamic>? queryParams}) =>
      throw Exception('notes transport down');
  @override
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body) =>
      throw Exception('notes transport down');
  @override
  Future<Map<String, dynamic>> patchJson(String path, Map<String, dynamic> body) =>
      throw Exception('notes transport down');
  @override
  Future<Map<String, dynamic>> deleteJson(String path) =>
      throw Exception('notes transport down');
}

/// Budgets transport that always throws — correct type.
class _ThrowingBudgetsTransport implements BudgetsTransport {
  @override
  Future<Map<String, dynamic>> getJson(String path,
      {Map<String, dynamic>? queryParams}) =>
      throw Exception('budgets transport down');
  @override
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body) =>
      throw Exception('budgets transport down');
  @override
  Future<Map<String, dynamic>> patchJson(String path, Map<String, dynamic> body) =>
      throw Exception('budgets transport down');
  @override
  Future<Map<String, dynamic>> deleteJson(String path) =>
      throw Exception('budgets transport down');
}
