import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/local/note_dao.dart';
import 'package:lazyclaw_mobile/local/task_dao.dart';
import 'package:lazyclaw_mobile/models/note.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/providers/notes_provider.dart';
import 'package:lazyclaw_mobile/providers/tasks_provider.dart';
import 'package:lazyclaw_mobile/sync/note_sync.dart';
import 'package:lazyclaw_mobile/sync/task_sync.dart';

/// A DAO whose cache read blows up — simulates a degraded / corrupt local DB so
/// we can prove `load()` never strands the screen on the loading skeleton.
class _ThrowingTaskDao implements TaskDao {
  @override
  Future<List<Task>> list() async => throw StateError('cache read failed');

  // Everything else is unreachable in this test (list() throws first). Forward
  // to noSuchMethod so we don't have to stub the full DAO surface.
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _ThrowingNoteDao implements NoteDao {
  @override
  Future<List<Note>> list() async => throw StateError('cache read failed');

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

/// A sync engine that does nothing and never throws — keeps the test focused on
/// the `load()` crash-safety path, not the sync engine.
class _NoopTaskSync implements TaskSync {
  @override
  bool get isRunning => false;
  @override
  Future<SyncResult> sync({bool retryRejected = false}) async => const SyncResult();
  @override
  Future<SyncResult> push() async => const SyncResult();
  @override
  Future<SyncResult> pull() async => const SyncResult();
}

class _NoopNoteSync implements NoteSync {
  @override
  bool get isRunning => false;
  @override
  Future<NoteSyncResult> sync() async => const NoteSyncResult();
  @override
  Future<NoteSyncResult> push() async => const NoteSyncResult();
  @override
  Future<NoteSyncResult> pull() async => const NoteSyncResult();
}

void main() {
  group('TasksNotifier.load is crash-safe', () {
    test('a throwing cache read clears isLoading and surfaces the error',
        () async {
      final n = TasksNotifier(_ThrowingTaskDao(), _NoopTaskSync());

      await n.load();
      // Let the fire-and-forget _syncThenRefresh settle (it ALSO re-reads the
      // throwing cache — it must not crash with an unhandled async error).
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(n.state.isLoading, isFalse,
          reason: 'screen must never be stuck on the loading skeleton');
      expect(n.state.error, isNotNull,
          reason: 'the read failure must be surfaced to the UI');
    });
  });

  group('NotesNotifier.load is crash-safe', () {
    test('a throwing cache read clears isLoading and surfaces the error',
        () async {
      final n = NotesNotifier(_ThrowingNoteDao(), _NoopNoteSync());

      await n.load();
      await Future<void>.delayed(const Duration(milliseconds: 20));

      expect(n.state.isLoading, isFalse,
          reason: 'screen must never be stuck on the loading skeleton');
      expect(n.state.error, isNotNull,
          reason: 'the read failure must be surfaced to the UI');
    });
  });
}
