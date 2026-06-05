import '../core/api/api_exceptions.dart';
import '../local/task_dao.dart';
import '../models/task.dart';
import '../repositories/tasks_repository.dart';

/// Raised when a push stops early because the network/server is unreachable.
/// The drained-so-far items are already removed from the outbox; the rest stay
/// queued for the next sync.
class _PushInterrupted implements Exception {
  final Object cause;
  _PushInterrupted(this.cause);
}

/// Outcome of one [TaskSync.sync] run — handy for tests + UI diagnostics.
class SyncResult {
  final int pushed;
  final int pulled;
  final int deletedApplied;
  final int conflicts;
  final bool pushInterrupted;
  final bool pullFailed;
  final Object? error;

  const SyncResult({
    this.pushed = 0,
    this.pulled = 0,
    this.deletedApplied = 0,
    this.conflicts = 0,
    this.pushInterrupted = false,
    this.pullFailed = false,
    this.error,
  });
}

/// The offline-first sync engine for tasks.
///
/// * [push] drains the outbox in order, calling the matching `/api/tasks*`
///   endpoint. On a network failure it STOPS (the failed item stays queued).
/// * [pull] fetches `GET /api/tasks/changes?since=<cursor>` and merges with
///   last-write-wins by `updated_at`; the loser of a real both-sides change is
///   recorded in `conflicts` (never silently dropped).
/// * [sync] = push() then pull(), guarded against concurrent runs.
class TaskSync {
  final TaskDao _dao;
  final TasksRepository _repo;

  bool _running = false;

  TaskSync(this._dao, this._repo);

  bool get isRunning => _running;

  /// push() then pull(). A second call while one is in flight is a no-op and
  /// returns an empty result.
  Future<SyncResult> sync() async {
    if (_running) return const SyncResult();
    _running = true;
    try {
      final pushResult = await push();
      final pullResult = await pull();
      return SyncResult(
        pushed: pushResult.pushed,
        pulled: pullResult.pulled,
        deletedApplied: pullResult.deletedApplied,
        conflicts: pullResult.conflicts,
        pushInterrupted: pushResult.pushInterrupted,
        pullFailed: pullResult.pullFailed,
        error: pushResult.error ?? pullResult.error,
      );
    } finally {
      _running = false;
    }
  }

  // ── PUSH ────────────────────────────────────────────────────────────────

  /// Drain the outbox in seq order. Returns how many items were pushed. On a
  /// network failure it stops early (remaining items retried next sync).
  Future<SyncResult> push() async {
    final queue = await _dao.readOutbox();
    var pushed = 0;
    for (final item in queue) {
      try {
        await _pushOne(item);
        await _dao.deleteOutboxItem(item.seq);
        // A delete that pushed lets the tombstone be hard-removed.
        await _dao.clearDirty(item.entityId);
        pushed++;
      } on _PushInterrupted catch (e) {
        // Network down — stop, keep the rest queued for the next run.
        return SyncResult(
          pushed: pushed,
          pushInterrupted: true,
          error: e.cause,
        );
      }
    }
    return SyncResult(pushed: pushed);
  }

  Future<void> _pushOne(OutboxItem item) async {
    final p = item.payload;
    try {
      switch (item.op) {
        case OutboxOp.create:
          await _repo.createTask(
            (p['title'] ?? '').toString(),
            id: p['id']?.toString() ?? item.entityId,
            description: p['description']?.toString(),
            category: p['category']?.toString(),
            priority: p['priority']?.toString(),
            dueDate: p['due_date']?.toString(),
            reminderAt: p['reminder_at']?.toString(),
            recurring: p['recurring']?.toString(),
          );
          break;
        case OutboxOp.update:
          await _repo.updateTask(item.entityId, _patchFrom(p));
          break;
        case OutboxOp.complete:
          await _repo.completeTask(item.entityId);
          break;
        case OutboxOp.delete:
          await _repo.deleteTask(item.entityId);
          break;
        default:
          // Unknown op — drop it (deleting the outbox row happens in push()).
          break;
      }
    } on ApiError catch (e) {
      // A 404 on complete/delete means the server already lost the row — treat
      // as success (idempotent) and let the item be dequeued. Other server
      // errors with a status code are NOT network failures, so they also
      // dequeue rather than wedging the whole queue forever.
      if (_isNetworkError(e)) {
        throw _PushInterrupted(e);
      }
      // Non-network ApiError → swallow so the queue can drain; the next pull
      // re-establishes server truth.
    } catch (e) {
      if (_isNetworkError(e)) throw _PushInterrupted(e);
      rethrow;
    }
  }

  Map<String, dynamic> _patchFrom(Map<String, dynamic> payload) {
    final out = Map<String, dynamic>.from(payload)..remove('id');
    return out;
  }

  // ── PULL ────────────────────────────────────────────────────────────────

  /// Fetch the server delta and merge with last-write-wins. Advances the
  /// cursor to the server's `now` on success. On a network failure it returns
  /// `pullFailed: true` and leaves the cursor untouched.
  Future<SyncResult> pull() async {
    final cursor = await _dao.getCursor();
    TaskChanges changes;
    try {
      changes = await _repo.fetchChanges(since: cursor);
    } catch (e) {
      return SyncResult(pullFailed: true, error: e);
    }

    var pulled = 0;
    var conflicts = 0;
    final nowIso = DateTime.now().toUtc().toIso8601String();

    for (final st in changes.tasks) {
      final applied = await _mergeServerTask(st, syncedAt: nowIso);
      if (applied.written) pulled++;
      if (applied.conflict) conflicts++;
    }

    var deletedApplied = 0;
    for (final id in changes.deleted) {
      await _dao.applyServerDelete(id, syncedAt: nowIso);
      deletedApplied++;
    }

    // Advance the cursor to the server clock to avoid local/server skew.
    if (changes.now.isNotEmpty) {
      await _dao.setCursor(changes.now);
    }

    return SyncResult(
      pulled: pulled,
      deletedApplied: deletedApplied,
      conflicts: conflicts,
    );
  }

  /// Apply last-write-wins for a single server task against the local cache.
  ///
  /// * No local row → write the server copy.
  /// * Local NOT dirty → server wins (write).
  /// * Local dirty:
  ///     - server `updated_at` >= local `updated_at` → server wins, and the
  ///       overwritten local edit is logged to `conflicts` (both sides changed).
  ///     - local strictly newer → keep local (don't clobber the user's
  ///       un-pushed edit; it will push on the next sync). No log needed.
  Future<_MergeOutcome> _mergeServerTask(
    ServerTask st, {
    required String syncedAt,
  }) async {
    final serverTask = st.task;
    final serverUpdatedAt = st.updatedAt;
    final localRow = await _dao.getRow(serverTask.id);

    if (localRow == null) {
      await _dao.upsertFromServer(
        serverTask,
        serverUpdatedAt: serverUpdatedAt,
        syncedAt: syncedAt,
      );
      return const _MergeOutcome(written: true, conflict: false);
    }

    final localDirty = ((localRow['dirty'] as int?) ?? 0) == 1;
    if (!localDirty) {
      await _dao.upsertFromServer(
        serverTask,
        serverUpdatedAt: serverUpdatedAt,
        syncedAt: syncedAt,
      );
      return const _MergeOutcome(written: true, conflict: false);
    }

    // Local is dirty — a genuine concurrent edit. Compare timestamps.
    final localUpdatedAt = (localRow['updated_at'] as String?) ?? '';
    final serverWins = _gte(serverUpdatedAt, localUpdatedAt);

    if (serverWins) {
      // Server wins; log the local edit we are about to overwrite.
      await _logFieldConflicts(localRow, serverTask, at: syncedAt);
      await _dao.upsertFromServer(
        serverTask,
        serverUpdatedAt: serverUpdatedAt,
        syncedAt: syncedAt,
      );
      return const _MergeOutcome(written: true, conflict: true);
    }

    // Local strictly newer — keep it; it re-pushes next sync. Nothing logged.
    return const _MergeOutcome(written: false, conflict: false);
  }

  /// Record each differing field as a conflict row so the loser is never lost.
  Future<void> _logFieldConflicts(
    Map<String, Object?> localRow,
    Task serverTask,
    {required String at}) async {
    const fields = <String, String>{
      'title': 'title',
      'description': 'description',
      'status': 'status',
      'priority': 'priority',
      'due_date': 'due_date',
      'category': 'category',
    };
    final serverJson = serverTask.toJson();
    for (final entry in fields.entries) {
      final col = entry.key;
      final localVal = localRow[col]?.toString();
      final serverVal = serverJson[col]?.toString();
      if (localVal != serverVal) {
        await _dao.logConflict(
          id: serverTask.id,
          field: col,
          local: localVal,
          server: serverVal,
          at: at,
        );
      }
    }
  }

  // ── helpers ──────────────────────────────────────────────────────────────

  /// True when [a] >= [b] as ISO-8601 strings (lexicographic == chronological
  /// for zero-padded ISO timestamps). Empty server time loses to any local time.
  static bool _gte(String? a, String? b) {
    final av = a ?? '';
    final bv = b ?? '';
    if (av.isEmpty) return false;
    if (bv.isEmpty) return true;
    return av.compareTo(bv) >= 0;
  }

  static bool _isNetworkError(Object e) {
    if (e is ApiError) {
      // status 0 == no response reached us (DNS/connection/timeout).
      return e.status == 0;
    }
    return true; // non-ApiError throws from the transport are network-ish.
  }
}

class _MergeOutcome {
  final bool written;
  final bool conflict;
  const _MergeOutcome({required this.written, required this.conflict});
}
