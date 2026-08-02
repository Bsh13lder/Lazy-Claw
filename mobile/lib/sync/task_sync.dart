import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';

import '../core/api/api_exceptions.dart';
import '../local/task_dao.dart';
import '../models/task.dart';
import '../repositories/tasks_repository.dart';
import 'sync_time.dart';

/// Raised when a push stops early because the network/server is unreachable, or
/// because a retryable server error (5xx) should keep the queue intact. The
/// drained-so-far items are already removed from the outbox; the rest stay
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
///   endpoint. On a network OR retryable-server failure it STOPS (the failed
///   item stays queued); only a definitive 4xx is allowed to drain.
/// * [pull] fetches `GET /api/tasks/changes?since=<cursor>` and merges with
///   last-write-wins by `updated_at`; the loser of a real both-sides change is
///   recorded in `conflicts` (never silently dropped).
/// * [sync] = push() then pull(), guarded against concurrent runs.
class TaskSync {
  final TaskDao _dao;
  final TasksRepository _repo;

  /// Invoked (best-effort) AFTER a server tombstone is applied for a task id,
  /// so the caller can cancel the task's scheduled local reminder alarms.
  ///
  /// WHY: a remote delete is the ONLY delete path that never passes through
  /// `TasksNotifier.deleteTask` (which cancels alarms itself), and the
  /// reconcile sweep (`TaskReminderService.syncAll`) only cancels ids inside
  /// the reserved ranges of tasks still returned by `dao.list()` — deleted
  /// rows are excluded, so without this hook the orphaned alarms kept firing.
  /// Null = no-op (tests / callers without notification wiring). Production
  /// wires `cancelTaskReminderAlarms`, which sweeps the task's FULL reserved
  /// notification-id range.
  final Future<void> Function(String taskId)? _onTaskTombstoned;

  /// A retryable (5xx) item is dead-lettered after this many failed attempts so
  /// one poison row can't wedge the whole queue forever.
  static const int kMaxPushAttempts = 5;

  bool _running = false;

  TaskSync(
    this._dao,
    this._repo, {
    Future<void> Function(String taskId)? onTaskTombstoned,
  }) : _onTaskTombstoned = onTaskTombstoned;

  bool get isRunning => _running;

  /// push() then pull(). A second call while one is in flight is a no-op and
  /// returns an empty result.
  Future<SyncResult> sync({bool retryRejected = false}) async {
    if (_running) return const SyncResult();
    _running = true;
    try {
      // Explicit user-triggered force-retry ("Sync now"): drop stale
      // create_rejected markers so an orphan rejected transiently (e.g. during an
      // outage) is no longer excluded from the self-heal below. Routine syncs pass
      // false, keeping the markers so a genuinely-broken create can't loop.
      if (retryRejected) {
        final cleared = await _dao.clearCreateRejectedConflicts();
        debugPrint(
          'TaskSync.sync: force-retry cleared $cleared create_rejected marker(s)',
        );
      }
      // Self-heal any stranded offline creates (ops dead-lettered or silently
      // drained by an older build) BEFORE draining, so a dirty cache row with no
      // outbox op re-pushes this run instead of living on-device forever,
      // invisible to the server. Mirrors BudgetsSync's reserva-1000 recovery.
      final healed = await _dao.reenqueueOrphanedCreates();
      if (healed > 0) {
        debugPrint(
          'TaskSync.sync: self-heal re-enqueued $healed stranded create(s)',
        );
      }
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
  /// network OR retryable-server failure it stops early (remaining items retried
  /// next sync). A 4xx (validation/conflict/404) is safe to drain.
  Future<SyncResult> push() async {
    final queue = await _dao.readOutbox();
    // H3: coalesce consecutive `update` ops per entity so replays can't
    // interleave with server-stamped times. The first update row of a run keeps
    // the merged payload; the rest are no-op'd (just dequeued).
    final coalesced = _coalesceUpdates(queue);
    if (queue.isNotEmpty) {
      debugPrint(
        'TaskSync.push: draining outbox — ${queue.length} op(s), '
        '${coalesced.skipSeqs.length} coalesced',
      );
    }

    var pushed = 0;
    for (final item in queue) {
      try {
        // Skipped duplicate update rows: just dequeue, no network call.
        if (coalesced.skipSeqs.contains(item.seq)) {
          debugPrint(
            'TaskSync.push: coalesced-skip seq=${item.seq} op=${item.op} '
            'id=${item.entityId}',
          );
          await _dao.deleteOutboxItem(item.seq);
          continue;
        }
        final effective = coalesced.payloads[item.seq] != null
            ? _withPayload(item, coalesced.payloads[item.seq]!)
            : item;

        final committed = await _pushOne(effective);
        if (committed) {
          // C2: retire the pushed item atomically (delete outbox row + clear
          // dirty / hard-remove tombstone) so a crash can't split the two writes.
          await _dao.commitPush(item.seq, item.entityId);
          pushed++;
          debugPrint(
            'TaskSync.push: pushed seq=${item.seq} op=${item.op} '
            'id=${item.entityId}',
          );
        }
        // A drained-but-not-committed item (definitive 4xx, or a dead-lettered
        // 5xx poison) has already had its outbox row removed inside the failure
        // classifier; we leave its cache row dirty so the NEXT pull restores
        // server truth — never silently dropping the user's edit.
      } on _PushInterrupted catch (e) {
        // Network down OR a retryable server error — stop, keep the rest queued.
        debugPrint(
          'TaskSync.push: interrupted after $pushed pushed — remaining outbox '
          'op(s) preserved for next sync: ${e.cause}',
        );
        return SyncResult(
          pushed: pushed,
          pushInterrupted: true,
          error: e.cause,
        );
      }
    }
    if (queue.isNotEmpty) {
      debugPrint('TaskSync.push: drain complete — $pushed pushed');
    }
    return SyncResult(pushed: pushed);
  }

  /// Push one queued op. Returns true when the server accepted it (so the
  /// caller commits the retire); false when the item was DRAINED on a definitive
  /// client error or dead-lettered as a 5xx poison (the failure classifier has
  /// already removed its outbox row, and the cache stays dirty for the next
  /// pull). Throws [_PushInterrupted] to STOP the drain and keep the queue.
  Future<bool> _pushOne(OutboxItem item) async {
    final p = item.payload;
    try {
      switch (item.op) {
        case OutboxOp.create:
          // The initial checklist rides the create POST body (decoded to the
          // `[{id,title,done}]` list CreateTaskBody expects). Only forward it
          // when present so a step-less create omits the key entirely.
          final rawSteps = p['steps'];
          await _repo.createTask(
            (p['title'] ?? '').toString(),
            id: p['id']?.toString() ?? item.entityId,
            description: p['description']?.toString(),
            category: p['category']?.toString(),
            priority: p['priority']?.toString(),
            dueDate: p['due_date']?.toString(),
            reminderAt: p['reminder_at']?.toString(),
            recurring: p['recurring']?.toString(),
            recurUntil: p['recur_until']?.toString(),
            steps: rawSteps == null ? null : _decodeSteps(rawSteps),
          );
          break;
        case OutboxOp.update:
          await _pushUpdate(item.entityId, p);
          break;
        case OutboxOp.complete:
          await _repo.completeTask(item.entityId);
          break;
        case OutboxOp.delete:
          await _repo.deleteTask(item.entityId);
          break;
        case OutboxOp.commentAdd:
          final c = Map<String, dynamic>.from((p['comment'] as Map?) ?? {});
          await _repo.addComment(item.entityId, {
            'id': c['id'],
            'text': c['text'],
            'subtask_id': c['subtask_id'],
          });
          break;
        case OutboxOp.commentDelete:
          await _repo.deleteComment(
              item.entityId, (p['comment_id'] ?? '').toString());
          break;
        default:
          // Unknown op — drop it (deleting the outbox row happens in push()).
          break;
      }
      // Server accepted the op → the caller commits the retire.
      return true;
    } catch (e) {
      // Drained (returns false) or interrupting (throws).
      return _classifyPushFailure(item, e);
    }
  }

  /// Decide what a push failure means and act on it. NEVER silently drops a
  /// queued edit on a transient failure (C1). Returns `true` ONLY for an
  /// idempotent 404-on-complete/delete/comment_delete (treated as a success →
  /// caller commits + counts it). For the other drain branches it removes THIS
  /// item's outbox row here and returns `false`, leaving the cache row dirty so
  /// the next pull
  /// re-establishes server truth. Throws [_PushInterrupted] to STOP the drain
  /// and keep the queue.
  ///   * network (timeout/connection/cancel, status 0, non-badResponse) →
  ///     [_PushInterrupted] (stop draining, keep ALL queued items);
  ///   * server 5xx → retryable: bump the attempt counter, dead-letter after
  ///     [kMaxPushAttempts], otherwise [_PushInterrupted] (keep it queued);
  ///   * 404 on complete/delete → idempotent success (return true → caller commits);
  ///   * other 4xx → drain the outbox row (return false; next pull restores it).
  Future<bool> _classifyPushFailure(OutboxItem item, Object e) async {
    if (_isNetworkError(e)) {
      debugPrint(
        'TaskSync.push: network/transport failure op=${item.op} '
        'id=${item.entityId} — stop drain, keep queue: $e',
      );
      throw _PushInterrupted(e);
    }
    final api = _asApiError(e);
    final status = api?.status ?? _statusOf(e) ?? 0;

    // 404 on complete/delete is idempotent success — the row is already gone
    // server-side. Return true so the caller commits (removes the outbox row
    // AND clears dirty) and counts it as pushed.
    if (status == 404 &&
        (item.op == OutboxOp.delete ||
            item.op == OutboxOp.complete ||
            item.op == OutboxOp.commentDelete)) {
      debugPrint(
        'TaskSync.push: HTTP 404 on ${item.op} id=${item.entityId} — '
        'idempotent success',
      );
      return true;
    }

    if (status >= 500) {
      // Retryable. Count the attempt; dead-letter a poison item, else stop and
      // keep it queued for the next sync — NEVER silently drop.
      final attempts = await _dao.bumpOutboxAttempts(item.seq);
      if (attempts >= kMaxPushAttempts) {
        debugPrint(
          'TaskSync.push: dead-lettered op=${item.op} id=${item.entityId} '
          'after $attempts attempt(s) (HTTP $status)',
        );
        await _dao.deadLetterOutboxItem(item.seq);
        return false; // drained the poison item; local stays dirty for next pull
      }
      debugPrint(
        'TaskSync.push: retryable HTTP $status op=${item.op} '
        'id=${item.entityId} attempt=$attempts — keeping queued',
      );
      throw _PushInterrupted(e);
    }

    if (status >= 400) {
      // Definitive client error (validation/conflict). For an update/complete/
      // delete this is safe to drain silently — the row exists server-side, so
      // the next pull restores server truth. But a CREATE is client-originated:
      // there is NO server truth to restore, so silently dropping the only
      // queued record permanently strands the dirty cache row (an orphan the
      // user can never re-sync). Log it as a visible conflict BEFORE draining
      // so the UI / next sync can surface the rejection.
      if (item.op == OutboxOp.create) {
        await _logCreateRejected(item, status);
      } else {
        debugPrint(
          'TaskSync.push: definitive HTTP $status op=${item.op} '
          'id=${item.entityId} — draining outbox row (next pull restores truth)',
        );
      }
      await _dao.deleteOutboxItem(item.seq);
      return false; // drained
    }

    // Unknown shape with no usable status → treat as network-ish and keep it.
    debugPrint(
      'TaskSync.push: unclassifiable failure op=${item.op} '
      'id=${item.entityId} — keeping queued: $e',
    );
    throw _PushInterrupted(e);
  }

  /// Record a rejected CREATE in the `conflicts` table so a definitive 4xx on a
  /// client-originated row is never silent. The local cache row stays dirty (its
  /// `dirty=1` flag is the UI's "un-synced" signal); this surfaces WHY it can't
  /// sync. The conflict row's `server` value is null (the create never landed)
  /// and the `local` value carries the entity label + rejecting status for
  /// diagnosis.
  Future<void> _logCreateRejected(OutboxItem item, int status) async {
    final p = item.payload;
    final label = p['title']?.toString() ?? '';
    final descriptor = label.isEmpty ? item.entity : '${item.entity}: $label';
    await _dao.logConflict(
      id: item.entityId,
      field: 'create_rejected',
      local: 'HTTP $status — $descriptor',
      server: null, // create never landed → no server value
    );
    debugPrint(
      'TaskSync.push: CREATE rejected HTTP $status entity=${item.entity} '
      'id=${item.entityId} — conflict logged, cache stays dirty',
    );
  }

  /// Push an `update` op. Plain task fields go via PATCH /api/tasks/{id}; the
  /// sub-task checklist (when the payload carries `steps`) rides the dedicated
  /// PUT /api/tasks/{id}/steps route, because PATCH's `UpdateTaskBody` does NOT
  /// accept `steps` and would silently drop them.
  ///
  /// The create-then-add-subtasks flow is safe: the outbox drains in seq order,
  /// so the entity's CREATE always pushes before any later steps-bearing update,
  /// and the task exists server-side by the time this PUT runs.
  ///
  /// Either call may throw — it propagates to [_pushOne]'s catch, where the
  /// shared classifier applies the SAME 5xx-retry / 4xx-drain / dead-letter
  /// handling as every other op. Both calls are idempotent on replay (PATCH and
  /// "replace-all" PUT), so a partial success (PATCH ok, PUT 5xx) safely re-runs.
  Future<void> _pushUpdate(String id, Map<String, dynamic> payload) async {
    final rawSteps = payload['steps'];
    final patch = _patchFrom(payload)..remove('steps');

    // tags is stored/queued as a JSON-array STRING (like steps) but the server's
    // UpdateTaskBody.tags is `list[str]`, so decode it to a list before PATCH.
    if (patch.containsKey('tags')) {
      patch['tags'] = _decodeTags(patch['tags']);
    }

    // Only PATCH when there is a real field to change. A steps-only edit leaves
    // just the synthetic client `updated_at` behind, and an otherwise-empty
    // UpdateTaskBody is rejected with a 400 — so skip the no-op PATCH entirely.
    if (_hasRealPatchFields(patch)) {
      await _repo.updateTask(id, patch);
    }
    if (rawSteps != null) {
      await _repo.setSteps(id, _decodeSteps(rawSteps));
    }
  }

  Map<String, dynamic> _patchFrom(Map<String, dynamic> payload) {
    final out = Map<String, dynamic>.from(payload)..remove('id');
    return out;
  }

  /// True when [patch] carries at least one server-applicable field beyond the
  /// synthetic `updated_at` LWW marker — i.e. the PATCH would actually change
  /// something (the backend 400s an empty update).
  static bool _hasRealPatchFields(Map<String, dynamic> patch) =>
      patch.keys.any((k) => k != 'updated_at');

  /// Decode the outbox `steps` payload into the `[{id?, title, done}]` list the
  /// PUT /steps body expects. The DAO stores `steps` as a JSON-array STRING;
  /// this also tolerates an already-decoded List. Null / malformed / empty
  /// input yields `[]` (a deliberate "clear the checklist" PUT).
  static List<Map<String, dynamic>> _decodeSteps(Object? raw) {
    dynamic decoded = raw;
    if (raw is String) {
      if (raw.trim().isEmpty) return const [];
      try {
        decoded = jsonDecode(raw);
      } catch (_) {
        return const [];
      }
    }
    if (decoded is! List) return const [];
    return decoded
        .whereType<Map>()
        .map((e) => Map<String, dynamic>.from(e))
        .toList();
  }

  /// Decode the outbox `tags` payload into the `List<String>` the server's
  /// `UpdateTaskBody.tags` (`list[str]`) expects. The DAO stores `tags` as a
  /// JSON-array STRING; this also tolerates an already-decoded list. Null /
  /// malformed / empty input yields `[]` (a deliberate "clear the tags" PATCH).
  static List<String> _decodeTags(Object? raw) {
    dynamic decoded = raw;
    if (raw is String) {
      if (raw.trim().isEmpty) return const [];
      try {
        decoded = jsonDecode(raw);
      } catch (_) {
        return const [];
      }
    }
    if (decoded is! List) return const [];
    return decoded.map((e) => e.toString()).toList();
  }

  /// Replace an item's payload (used for the coalesced update head).
  OutboxItem _withPayload(OutboxItem item, Map<String, dynamic> payload) =>
      OutboxItem(
        seq: item.seq,
        op: item.op,
        entity: item.entity,
        entityId: item.entityId,
        payload: payload,
        createdAt: item.createdAt,
        attempts: item.attempts,
      );

  // ── PULL ────────────────────────────────────────────────────────────────

  /// Fetch the server delta and merge with last-write-wins. Advances the
  /// cursor to the server's `now` on success. On a network failure it returns
  /// `pullFailed: true` and leaves the cursor untouched.
  Future<SyncResult> pull() async {
    final cursor = await _dao.getCursor();
    debugPrint('TaskSync.pull: fetching changes since cursor=$cursor');
    TaskChanges changes;
    try {
      changes = await _repo.fetchChanges(since: cursor);
    } catch (e) {
      debugPrint('TaskSync.pull: fetchChanges failed — cursor unchanged: $e');
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
      final logged = await _applyServerTombstone(id, syncedAt: nowIso);
      if (logged) conflicts++;
      deletedApplied++;
      await _cancelRemindersForTombstone(id);
    }
    debugPrint(
      'TaskSync.pull: applied — merged=$pulled deleted=$deletedApplied '
      'conflicts=$conflicts',
    );

    // M3: advance the cursor only when the server gave us a real clock. An
    // empty `now` means we can't trust the page boundary — fall back to the max
    // `updated_at` we actually observed; if even that is empty, treat the pull
    // as FAILED so we don't silently skip the delta on the next run.
    final nextCursor = _resolveCursor(changes);
    if (nextCursor != null && nextCursor.isNotEmpty) {
      await _dao.setCursor(nextCursor);
      debugPrint('TaskSync.pull: cursor advanced → $nextCursor');
    } else {
      debugPrint(
        'TaskSync.pull: empty server clock with no datable rows — '
        'pull marked failed, cursor held',
      );
      return SyncResult(
        pulled: pulled,
        deletedApplied: deletedApplied,
        conflicts: conflicts,
        pullFailed: true,
        error: StateError('server returned empty `now` with no datable rows'),
      );
    }

    return SyncResult(
      pulled: pulled,
      deletedApplied: deletedApplied,
      conflicts: conflicts,
    );
  }

  /// The cursor to advance to: the server `now` when present (shifted back by
  /// [_cursorOverlap] — see [_withOverlap]), else the newest `updated_at`
  /// across the page's tasks (so we never re-fetch from scratch on a server
  /// that omitted `now`). Null/empty → caller treats as a pull failure.
  String? _resolveCursor(TaskChanges changes) {
    if (changes.now.isNotEmpty) return _withOverlap(changes.now);
    String best = '';
    for (final st in changes.tasks) {
      final ua = st.updatedAt ?? '';
      if (ua.isNotEmpty && ua.compareTo(best) > 0) best = ua;
    }
    return best.isEmpty ? null : best;
  }

  /// Small overlap window subtracted from the server clock before it is
  /// persisted as the next `since=`. The server filters `updated_at > since`,
  /// so advancing the cursor to the EXACT moment the page was read can strand
  /// a row that committed between the server's `now_iso` stamp
  /// (tasks/store.py:2351) and its SELECT (:2355) — permanently, since every
  /// later pull's `since` is already past it. Re-delivery of an
  /// already-applied row is a harmless no-op (`_mergeServerTask` is
  /// idempotent — upsert + last-write-wins), so a small overlap can only
  /// cause a few rows to be re-checked, never a duplicate or a loss.
  static const Duration _cursorOverlap = Duration(seconds: 2);

  /// Parse [rawNow], subtract [_cursorOverlap], and re-serialise via
  /// [_formatServerCursor]. Falls back to the raw string verbatim when it
  /// can't be parsed as a date — a clock format hiccup from the server must
  /// never crash the sync.
  String _withOverlap(String rawNow) {
    final parsed = DateTime.tryParse(rawNow);
    if (parsed == null) return rawNow;
    return _formatServerCursor(parsed.subtract(_cursorOverlap));
  }

  /// Format [dt] to byte-match the server's own timestamp convention:
  /// `YYYY-MM-DDTHH:MM:SS.ffffff+00:00` (UTC, 6-digit microseconds, literal
  /// `+00:00`).
  ///
  /// MUST NOT use `DateTime.toIso8601String()` here. The server
  /// (`lazyclaw/tasks/store.py:2351`) stamps `now` — and every row's
  /// `updated_at` — via Python's `datetime.now(timezone.utc).isoformat()`,
  /// which always renders `+00:00`, never `Z`. `get_task_changes`
  /// (store.py:2353-2359) compares `WHERE updated_at > ?` as raw SQLite
  /// TEXT — a byte-for-byte LEXICAL comparison, not a parsed-datetime one.
  /// Dart's `toIso8601String()` emits `Z` instead of `+00:00` and OMITS the
  /// microsecond digits entirely when they're zero. `Z` (0x5A) sorts AFTER
  /// any digit and `.` (0x2E) sorts AFTER `+` (0x2B), so a cursor built with
  /// `toIso8601String()` can lexically compare GREATER than a real,
  /// chronologically-LATER server row — silently re-introducing the exact
  /// orphaning bug this overlap window exists to fix, on every ordinary
  /// pull. This formatter exists solely to byte-match the server's
  /// convention so the comparison behaves as a real datetime comparison.
  String _formatServerCursor(DateTime dt) {
    final utc = dt.isUtc ? dt : dt.toUtc();
    String pad(int n, int width) => n.toString().padLeft(width, '0');
    final micros = utc.millisecond * 1000 + utc.microsecond;
    return '${pad(utc.year, 4)}-${pad(utc.month, 2)}-${pad(utc.day, 2)}'
        'T${pad(utc.hour, 2)}:${pad(utc.minute, 2)}:${pad(utc.second, 2)}'
        '.${pad(micros, 6)}+00:00';
  }

  /// Best-effort: cancel the local reminder alarms of a freshly tombstoned
  /// task via the injected [_onTaskTombstoned] hook. A cancel failure must
  /// never fail the pull — the tombstone itself is already applied.
  Future<void> _cancelRemindersForTombstone(String id) async {
    final hook = _onTaskTombstoned;
    if (hook == null) return;
    try {
      await hook(id);
    } catch (e) {
      debugPrint(
        'TaskSync.pull: reminder cancel failed for tombstoned id=$id '
        '(non-fatal): $e',
      );
    }
  }

  /// Apply a server tombstone with H1 safety: if the local row has an UNSYNCED
  /// edit (dirty=1), log the server-delete-vs-local-values conflict BEFORE
  /// applying, drop the now-moot queued outbox op, then apply the delete — all
  /// in ONE transaction so a concurrent write can't slip in. Delete-wins is the
  /// policy, but it is never silent. Returns true when a conflict was logged.
  Future<bool> _applyServerTombstone(String id, {required String syncedAt}) {
    return _dao.runInTransaction<bool>((txn) async {
      final localRow = await txn.getRow(id);
      var loggedConflict = false;

      if (localRow != null) {
        final dirty = ((localRow['dirty'] as int?) ?? 0) == 1;
        final alreadyDeleted = ((localRow['deleted'] as int?) ?? 0) == 1;
        if (dirty && !alreadyDeleted) {
          // The user has an un-pushed edit the server delete is about to clobber.
          await _logTombstoneConflict(txn, localRow, at: syncedAt);
          loggedConflict = true;
        }
        if (dirty) {
          // Reconcile: the row is gone server-side, so replaying its queued ops
          // is pointless (or would 404). Drop them.
          await txn.deleteOutboxForEntity(id);
        }
      }

      await txn.applyServerDelete(id, syncedAt: syncedAt);
      return loggedConflict;
    });
  }

  /// Record each non-empty local field as a conflict against the server delete
  /// (server value is null — the row no longer exists).
  Future<void> _logTombstoneConflict(
    TaskTxn txn,
    Map<String, Object?> localRow, {
    required String at,
  }) async {
    const cols = <String>[
      'title',
      'description',
      'status',
      'priority',
      'due_date',
      'category',
    ];
    final id = (localRow['id'] as String?) ?? '';
    for (final col in cols) {
      final localVal = localRow[col]?.toString();
      if (localVal == null || localVal.isEmpty) continue;
      debugPrint(
        'TaskSync.pull: tombstone conflict id=$id field=$col '
        'winner=server(delete) — local edit lost',
      );
      await txn.logConflict(
        id: id,
        field: col,
        local: localVal,
        server: null, // server-deleted → no server value
        at: at,
      );
    }
  }

  /// Apply last-write-wins for a single server task against the local cache.
  ///
  /// The dirty-check + the upsert run in ONE transaction (H2) so a concurrent
  /// local write can't land between the read and the server-write.
  ///
  /// * No local row → write the server copy.
  /// * Local NOT dirty → server wins (write).
  /// * Local dirty:
  ///     - server `updated_at` >= local `updated_at` → server wins, and the
  ///       overwritten local edit is logged to `conflicts` (both sides changed).
  ///       Spurious "the server just echoed my own push" diffs are NOT logged.
  ///     - local strictly newer → keep local (don't clobber the user's
  ///       un-pushed edit; it will push on the next sync). No log needed.
  Future<_MergeOutcome> _mergeServerTask(
    ServerTask st, {
    required String syncedAt,
  }) {
    final serverTask = st.task;
    final serverUpdatedAt = st.updatedAt;

    return _dao.runInTransaction<_MergeOutcome>((txn) async {
      final localRow = await txn.getRow(serverTask.id);

      if (localRow == null) {
        await txn.upsertFromServer(
          serverTask,
          serverUpdatedAt: serverUpdatedAt,
          syncedAt: syncedAt,
        );
        return const _MergeOutcome(written: true, conflict: false);
      }

      final localDirty = ((localRow['dirty'] as int?) ?? 0) == 1;
      if (!localDirty) {
        await txn.upsertFromServer(
          serverTask,
          serverUpdatedAt: serverUpdatedAt,
          syncedAt: syncedAt,
        );
        return const _MergeOutcome(written: true, conflict: false);
      }

      // Local is dirty — a genuine concurrent edit. Compare timestamps.
      final localUpdatedAt = (localRow['updated_at'] as String?) ?? '';
      final serverWins = serverWinsByTime(serverUpdatedAt, localUpdatedAt);

      if (serverWins) {
        // Server wins; log the local edit we are about to overwrite. Only real
        // divergences are logged (a server row that merely echoes our just-
        // pushed values produces no diff → no spurious conflict — H3).
        final logged =
            await _logFieldConflicts(txn, localRow, serverTask, at: syncedAt);
        await txn.upsertFromServer(
          serverTask,
          serverUpdatedAt: serverUpdatedAt,
          syncedAt: syncedAt,
        );
        return _MergeOutcome(written: true, conflict: logged);
      }

      // Local strictly newer — keep it; it re-pushes next sync. Nothing logged.
      debugPrint(
        'TaskSync.pull: kept newer local id=${serverTask.id} — server change '
        'deferred, will re-push',
      );
      return const _MergeOutcome(written: false, conflict: false);
    });
  }

  /// Record each differing field as a conflict row so the loser is never lost.
  /// Returns true when at least one field actually differed (so a server echo
  /// of our own push logs nothing — H3 / M1).
  Future<bool> _logFieldConflicts(
    TaskTxn txn,
    Map<String, Object?> localRow,
    Task serverTask, {
    required String at,
  }) async {
    const fields = <String>[
      'title',
      'description',
      'status',
      'priority',
      'due_date',
      'category',
    ];
    final serverJson = serverTask.toJson();
    var any = false;
    for (final col in fields) {
      final localVal = localRow[col]?.toString();
      final serverVal = serverJson[col]?.toString();
      if (localVal != serverVal) {
        any = true;
        debugPrint(
          'TaskSync.pull: LWW field conflict id=${serverTask.id} field=$col '
          'winner=server',
        );
        await txn.logConflict(
          id: serverTask.id,
          field: col,
          local: localVal,
          server: serverVal,
          at: at,
        );
      }
    }
    return any;
  }

  // ── update coalescing (H3) ────────────────────────────────────────────────

  /// Merge consecutive pending `update` ops for the same entity into one
  /// payload carried by the FIRST update row; the later rows are marked to be
  /// dequeued without a network call. Each merged head also carries the client
  /// `updated_at` (latest local edit time) so the server can honor client LWW.
  _Coalesced _coalesceUpdates(List<OutboxItem> queue) {
    // entityId → seq of the head update row that will carry the merged payload.
    final head = <String, int>{};
    // head seq → merged payload (mutable while folding).
    final merged = <int, Map<String, dynamic>>{};
    final skip = <int>{};

    for (final item in queue) {
      if (item.op != OutboxOp.update) continue;
      final id = item.entityId;
      final headSeq = head[id];
      if (headSeq == null) {
        head[id] = item.seq;
        merged[item.seq] = Map<String, dynamic>.from(item.payload);
      } else {
        // Fold this later update into the head; later values win (LWW).
        final into = merged[headSeq]!;
        for (final entry in item.payload.entries) {
          if (entry.key == 'id') continue;
          into[entry.key] = entry.value;
        }
        skip.add(item.seq);
      }
    }

    // Stamp the client updated_at on each coalesced head so the server can LWW.
    for (final entry in merged.entries) {
      entry.value['updated_at'] = _lwwTimeFor(queue, entry.key);
    }

    return _Coalesced(payloads: merged, skipSeqs: skip);
  }

  /// The newest local edit time to advertise for the entity owning [headSeq] —
  /// the createdAt of the LAST update row folded into the head (queue order is
  /// chronological), falling back to the head's own createdAt.
  String _lwwTimeFor(List<OutboxItem> queue, int headSeq) {
    final headItem = queue.firstWhere((i) => i.seq == headSeq);
    var best = headItem.createdAt;
    for (final i in queue) {
      if (i.op == OutboxOp.update &&
          i.entityId == headItem.entityId &&
          i.createdAt.compareTo(best) > 0) {
        best = i.createdAt;
      }
    }
    return best;
  }

  // ── helpers ──────────────────────────────────────────────────────────────

  /// Unwrap an [ApiError] from either a bare throw or a [DioException] whose
  /// `.error` carries the real [ApiError] (the production `_ErrorInterceptor`
  /// shape). Returns null when [e] is neither.
  static ApiError? _asApiError(Object e) => e is ApiError
      ? e
      : (e is DioException && e.error is ApiError
          ? e.error as ApiError
          : null);

  /// Best-effort HTTP status for [e] across both error shapes.
  static int? _statusOf(Object e) {
    final api = _asApiError(e);
    if (api != null) return api.status;
    if (e is DioException) return e.response?.statusCode;
    return null;
  }

  /// True when [e] is a transport-level failure that should STOP the drain and
  /// keep the queue intact — robust to both the [ApiError] and the
  /// [DioException] shapes thrown in production.
  static bool _isNetworkError(Object e) {
    final api = _asApiError(e);
    if (api != null) {
      // status 0 == no response reached us (DNS/connection/timeout).
      return api.status == 0;
    }
    if (e is DioException) {
      // Anything that isn't a real HTTP response is a transport failure.
      if (e.type != DioExceptionType.badResponse) return true;
      // A badResponse with no status code is also network-ish.
      return e.response?.statusCode == null;
    }
    // A wholly unknown throw is not classifiable as a definitive server error;
    // be conservative and DON'T treat it as network here — the caller will fall
    // through to the unknown-shape branch and keep the item queued.
    return false;
  }
}

class _MergeOutcome {
  final bool written;
  final bool conflict;
  const _MergeOutcome({required this.written, required this.conflict});
}

/// Result of folding pending `update` ops: per-head merged payloads + the set of
/// later update rows to dequeue without a network call.
class _Coalesced {
  final Map<int, Map<String, dynamic>> payloads;
  final Set<int> skipSeqs;
  const _Coalesced({required this.payloads, required this.skipSeqs});
}
