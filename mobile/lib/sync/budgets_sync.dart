import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';

import '../core/api/api_exceptions.dart';
import '../local/budgets_dao.dart';
import '../models/budget_entry.dart';
import '../models/expense.dart';
import '../models/project.dart';
import '../repositories/budgets_repository.dart';
import 'sync_time.dart';

/// Raised when a push stops early because the network/server is unreachable, or
/// because a retryable server error (5xx) should keep the queue intact. The
/// drained-so-far items are already removed from the outbox; the rest stay
/// queued for the next sync.
class _PushInterrupted implements Exception {
  final Object cause;
  _PushInterrupted(this.cause);
}

/// Outcome of one [BudgetsSync.sync] run — handy for tests + UI diagnostics.
class BudgetsSyncResult {
  final int pushed;
  final int pulled;
  final int deletedApplied;
  final int conflicts;
  final bool pushInterrupted;
  final bool pullFailed;
  final Object? error;

  const BudgetsSyncResult({
    this.pushed = 0,
    this.pulled = 0,
    this.deletedApplied = 0,
    this.conflicts = 0,
    this.pushInterrupted = false,
    this.pullFailed = false,
    this.error,
  });
}

/// The offline-first sync engine for budgets (projects + expenses together).
///
/// Mirrors `TaskSync` exactly. The two entities share ONE delta feed
/// (`GET /api/budgets/changes`) and therefore ONE cursor.
///
/// * [push] drains the budgets outbox in seq order, routing each op to the
///   matching `/api/budgets/*` endpoint. On a network OR retryable-server
///   failure it STOPS (the failed item stays queued); only a definitive 4xx is
///   allowed to drain.
/// * [pull] fetches the changes feed and merges projects + expenses + both
///   tombstone lists with last-write-wins by `updated_at`; the loser of a real
///   both-sides change is recorded in `conflicts` (never silently dropped).
/// * [sync] = push() then pull(), guarded against concurrent runs.
class BudgetsSync {
  final BudgetsDao _dao;
  final BudgetsRepository _repo;

  /// A retryable (5xx) item is dead-lettered after this many failed attempts so
  /// one poison row can't wedge the whole queue forever.
  static const int kMaxPushAttempts = 5;

  bool _running = false;

  /// Set when a sync() is requested while one is already in flight. The running
  /// drain may have snapshotted the outbox BEFORE that mutation was queued, so
  /// it drains once more before finishing — otherwise a fresh add can sit
  /// unsynced until the next unrelated trigger (the reserva "didn't sync at add
  /// time" bug that made the user re-add the same expense and create duplicates).
  bool _pendingRerun = false;
  bool _pendingRetryRejected = false;

  BudgetsSync(this._dao, this._repo);

  bool get isRunning => _running;

  /// push() then pull(). A call arriving while one is in flight is NOT dropped —
  /// it requests a single coalesced re-drain so a mutation queued mid-sync still
  /// reaches the server this cycle.
  Future<BudgetsSyncResult> sync({bool retryRejected = false}) async {
    if (_running) {
      _pendingRerun = true;
      _pendingRetryRejected = _pendingRetryRejected || retryRejected;
      return const BudgetsSyncResult();
    }
    _running = true;
    try {
      var result = await _drainOnce(retryRejected: retryRejected);
      // Coalesce: keep draining while mutations landed during the last drain.
      // Bounded — each pass empties more of the outbox and a re-run is only
      // requested by a concurrent sync() call.
      while (_pendingRerun) {
        _pendingRerun = false;
        final rr = _pendingRetryRejected;
        _pendingRetryRejected = false;
        result = await _drainOnce(retryRejected: rr);
      }
      return result;
    } finally {
      _running = false;
      _pendingRerun = false;
      _pendingRetryRejected = false;
    }
  }

  /// One self-heal + push() + pull() drain.
  Future<BudgetsSyncResult> _drainOnce({bool retryRejected = false}) async {
    // Explicit user-triggered force-retry ("Sync now"): drop stale
    // create_rejected markers so a transiently-rejected orphan (e.g. the
    // reserva-1000 dropped by an outage or parent-404) is no longer excluded
    // from the self-heal below. Routine syncs pass false, keeping the markers.
    if (retryRejected) {
      final cleared = await _dao.clearCreateRejectedConflicts();
      debugPrint(
        'BudgetsSync.sync: force-retry cleared $cleared create_rejected marker(s)',
      );
    }
    // Self-heal any stranded offline creates (ops that were dead-lettered or
    // silently drained by an older build) BEFORE draining, so the reserva-1000
    // class of orphan — a dirty cache row with no outbox op — re-pushes this
    // run instead of living on-device forever, invisible to the server.
    final healed = await _dao.reenqueueOrphanedCreates();
    if (healed > 0) {
      debugPrint(
        'BudgetsSync.sync: self-heal re-enqueued $healed stranded create(s)',
      );
    }
    final pushResult = await push();
    final pullResult = await pull();
    return BudgetsSyncResult(
      pushed: pushResult.pushed,
      pulled: pullResult.pulled,
      deletedApplied: pullResult.deletedApplied,
      conflicts: pullResult.conflicts,
      pushInterrupted: pushResult.pushInterrupted,
      pullFailed: pullResult.pullFailed,
      error: pushResult.error ?? pullResult.error,
    );
  }

  // ── PUSH ────────────────────────────────────────────────────────────────

  /// Drain the budgets outbox in seq order. Returns how many items were pushed.
  /// On a network OR retryable-server failure it stops early (remaining items
  /// retried next sync). A 4xx (validation/conflict/404) is safe to drain.
  Future<BudgetsSyncResult> push() async {
    final queue = await _dao.readBudgetsOutbox();
    // Coalesce consecutive `update` ops per entity so multiple pending PATCHes
    // can't replay as separate round-trips that interleave with server-stamped
    // times. The first update row of a run keeps the merged payload (carrying a
    // client `updated_at` for LWW); the rest are no-op'd (just dequeued). Both
    // projects and expenses support `update`, and each is keyed by its own id.
    final coalesced = _coalesceUpdates(queue);
    if (queue.isNotEmpty) {
      debugPrint(
        'BudgetsSync.push: draining outbox — ${queue.length} op(s), '
        '${coalesced.skipSeqs.length} coalesced',
      );
    }

    var pushed = 0;
    for (final item in queue) {
      try {
        // Skipped duplicate update rows: just dequeue, no network call.
        if (coalesced.skipSeqs.contains(item.seq)) {
          debugPrint(
            'BudgetsSync.push: coalesced-skip seq=${item.seq} op=${item.op} '
            'entity=${item.entity} id=${item.entityId}',
          );
          await _dao.deleteOutboxItem(item.seq);
          continue;
        }
        final effective = coalesced.payloads[item.seq] != null
            ? _withPayload(item, coalesced.payloads[item.seq]!)
            : item;

        final committed = await _pushOne(effective);
        if (committed) {
          // Retire the pushed item atomically (delete outbox row + clear dirty
          // / hard-remove tombstone) so a crash can't split the two writes.
          await _dao.commitPush(item.seq, item.entity, item.entityId);
          pushed++;
          debugPrint(
            'BudgetsSync.push: pushed seq=${item.seq} op=${item.op} '
            'entity=${item.entity} id=${item.entityId}',
          );
        }
        // A drained-but-not-committed item (definitive 4xx, or a dead-lettered
        // 5xx poison) has already had its outbox row removed inside the failure
        // classifier; we leave its cache row dirty so the NEXT pull restores
        // server truth — never silently dropping the user's edit.
      } on _PushInterrupted catch (e) {
        debugPrint(
          'BudgetsSync.push: interrupted after $pushed pushed — remaining '
          'outbox op(s) preserved for next sync: ${e.cause}',
        );
        return BudgetsSyncResult(
          pushed: pushed,
          pushInterrupted: true,
          error: e.cause,
        );
      }
    }
    if (queue.isNotEmpty) {
      debugPrint('BudgetsSync.push: drain complete — $pushed pushed');
    }
    return BudgetsSyncResult(pushed: pushed);
  }

  /// Push one queued op. Returns true when the server accepted it (so the caller
  /// commits the retire); false when the item was DRAINED on a definitive
  /// client error or dead-lettered as a 5xx poison (the failure classifier has
  /// already removed its outbox row, and the cache stays dirty for the next
  /// pull). Throws [_PushInterrupted] to STOP the drain and keep the queue.
  Future<bool> _pushOne(BudgetsOutboxItem item) async {
    final p = item.payload;
    try {
      if (item.isProject) {
        switch (item.op) {
          case BudgetsOutboxOp.create:
            await _repo.createProject(
              (p['name'] ?? '').toString(),
              id: p['id']?.toString() ?? item.entityId,
              budget: _asDouble(p['budget']),
              description: p['description']?.toString(),
              color: p['color']?.toString(),
            );
            break;
          case BudgetsOutboxOp.update:
            await _repo.updateProject(item.entityId, _patchFrom(p));
            break;
          case BudgetsOutboxOp.delete:
            await _repo.deleteProject(item.entityId);
            break;
          default:
            break;
        }
      } else if (item.isExpense) {
        switch (item.op) {
          case BudgetsOutboxOp.create:
            // The project id is carried in the payload so the create can target
            // the right `/projects/{id}/expenses` URL on replay.
            await _repo.createExpense(
              (p['project_id'] ?? '').toString(),
              _asDouble(p['amount']) ?? 0.0,
              (p['description'] ?? '').toString(),
              id: p['id']?.toString() ?? item.entityId,
              vendor: p['vendor']?.toString(),
              currency: p['currency']?.toString(),
              spentAt: p['spent_at']?.toString(),
              notes: p['notes']?.toString(),
            );
            break;
          case BudgetsOutboxOp.update:
            await _repo.updateExpense(item.entityId, _patchFrom(p));
            break;
          case BudgetsOutboxOp.delete:
            await _repo.deleteExpense(item.entityId);
            break;
          default:
            break;
        }
      } else if (item.isBudgetEntry) {
        switch (item.op) {
          case BudgetsOutboxOp.create:
            // Idempotent on the client id: a retried POST returns the existing
            // row WITHOUT re-bumping the project budget — so a re-push after an
            // interrupted drain can't double-credit.
            await _repo.addBudgetEntry(
              (p['project_id'] ?? '').toString(),
              _asDouble(p['amount']) ?? 0.0,
              id: p['id']?.toString() ?? item.entityId,
              source: p['source']?.toString(),
              currency: p['currency']?.toString(),
            );
            break;
          case BudgetsOutboxOp.update:
            await _repo.updateBudgetEntry(
              item.entityId,
              amount: _asDouble(p['amount']),
              source: p['source']?.toString(),
              currency: p['currency']?.toString(),
            );
            break;
          case BudgetsOutboxOp.delete:
            await _repo.deleteBudgetEntry(item.entityId);
            break;
          default:
            break;
        }
      }
      // Server accepted the op → the caller commits the retire.
      return true;
    } catch (e) {
      // Drained (returns false) or interrupting (throws). A 404-on-delete is an
      // idempotent success and returns true so it's counted + committed by the
      // caller — never a silent success otherwise.
      return _classifyPushFailure(item, e);
    }
  }

  /// Decide what a push failure means and act on it. NEVER silently drops a
  /// queued edit on a transient failure. Returns `true` ONLY for an idempotent
  /// 404-on-delete (treated as a success → caller commits + counts it). For the
  /// other drain branches it removes THIS item's outbox row here and returns
  /// `false`, leaving the cache row dirty so the next pull re-establishes server
  /// truth. Throws [_PushInterrupted] to STOP the drain and keep the queue.
  ///   * network (timeout/connection/cancel, status 0, non-badResponse) →
  ///     [_PushInterrupted] (stop draining, keep ALL queued items);
  ///   * server 5xx → retryable: bump the attempt counter, dead-letter (drop the
  ///     poison outbox row) after [kMaxPushAttempts], otherwise [_PushInterrupted]
  ///     (keep it queued);
  ///   * 404 on delete → idempotent success (return true → caller commits);
  ///   * other 4xx → drain the outbox row (return false; next pull restores it).
  Future<bool> _classifyPushFailure(BudgetsOutboxItem item, Object e) async {
    if (_isNetworkError(e)) {
      debugPrint(
        'BudgetsSync.push: network/transport failure op=${item.op} '
        'entity=${item.entity} id=${item.entityId} — stop drain, keep queue: $e',
      );
      throw _PushInterrupted(e);
    }
    final api = _asApiError(e);
    final status = api?.status ?? _statusOf(e) ?? 0;

    // 404 on delete is idempotent success — the row is already gone server-side.
    // Return true so the caller commits (removes the outbox row AND hard-removes
    // the local tombstone) and counts it as pushed.
    if (status == 404 && item.op == BudgetsOutboxOp.delete) {
      debugPrint(
        'BudgetsSync.push: HTTP 404 on ${item.op} entity=${item.entity} '
        'id=${item.entityId} — idempotent success',
      );
      return true;
    }

    // 404 on a CREATE means the PARENT is missing server-side. An expense create
    // (`POST /projects/{id}/expenses`) 404s ONLY when the project row doesn't
    // exist yet for this user (see budgets.py `create_expense_route` → the route
    // 404s exclusively on `store.create_expense`'s "project not found"). Unlike a
    // 400/422 validation reject, this is TRANSIENT: the parent project may still
    // be queued ahead of a stalled drain, or land via a later pull. So RETRY
    // (keep queued, dead-letter after kMaxPushAttempts) instead of stranding the
    // child on the very first failure — this is the reserva-1000 data-loss fix.
    // A definitively-unresolvable 404 still bounds out and logs a conflict.
    if (status == 404 && item.op == BudgetsOutboxOp.create) {
      debugPrint(
        'BudgetsSync.push: HTTP 404 on CREATE entity=${item.entity} '
        'id=${item.entityId} — parent missing server-side, retry-or-deadletter',
      );
      return _retryOrDeadLetter(item, e, logRejectionOnDeadLetter: true);
    }

    if (status >= 500) {
      // Retryable server error.
      return _retryOrDeadLetter(item, e);
    }

    if (status >= 400) {
      // Definitive client error (validation/conflict). For an update/delete this
      // is safe to drain silently — the row exists server-side, so the next pull
      // restores server truth and the local dirty row reconciles. But a CREATE
      // is client-originated: there is NO server truth to restore, so silently
      // dropping the only queued record permanently strands the dirty cache row
      // (an orphan the user can never re-sync). Log it as a visible conflict
      // BEFORE draining so the UI / next sync can surface the rejection.
      if (item.op == BudgetsOutboxOp.create) {
        await _logCreateRejected(item, status);
      } else {
        debugPrint(
          'BudgetsSync.push: definitive HTTP $status op=${item.op} '
          'entity=${item.entity} id=${item.entityId} — draining outbox row '
          '(next pull restores truth)',
        );
      }
      await _dao.deleteOutboxItem(item.seq);
      return false; // drained
    }

    // Unknown shape with no usable status → treat as network-ish and keep it.
    debugPrint(
      'BudgetsSync.push: unclassifiable failure op=${item.op} '
      'entity=${item.entity} id=${item.entityId} — keeping queued: $e',
    );
    throw _PushInterrupted(e);
  }

  /// Keep a transiently-failed push queued and bump its attempt counter; once it
  /// has failed [kMaxPushAttempts] times, dead-letter it (drop just the outbox
  /// row, leave the cache row dirty) so one poison item can't wedge the queue
  /// forever. NEVER silently drops. Shared by the 5xx path and the 404-parent-
  /// not-found create path. When [logRejectionOnDeadLetter] is set, a
  /// `create_rejected` conflict is logged at dead-letter time so a create that
  /// never found its parent still surfaces to the UI. Returns `false` when
  /// dead-lettered; throws [_PushInterrupted] to STOP the drain and keep the
  /// queue while retries remain.
  Future<bool> _retryOrDeadLetter(
    BudgetsOutboxItem item,
    Object e, {
    bool logRejectionOnDeadLetter = false,
  }) async {
    final attempts = await _dao.bumpOutboxAttempts(item.seq);
    if (attempts >= kMaxPushAttempts) {
      if (logRejectionOnDeadLetter) {
        final status = _asApiError(e)?.status ?? _statusOf(e) ?? 0;
        await _logCreateRejected(item, status);
      }
      debugPrint(
        'BudgetsSync.push: dead-lettered op=${item.op} entity=${item.entity} '
        'id=${item.entityId} after $attempts attempt(s)',
      );
      await _dao.deadLetterOutboxItem(item.seq);
      return false; // drained the poison item; local stays dirty for next pull
    }
    debugPrint(
      'BudgetsSync.push: retryable failure op=${item.op} entity=${item.entity} '
      'id=${item.entityId} attempt=$attempts — keeping queued',
    );
    throw _PushInterrupted(e);
  }

  /// Record a rejected CREATE in the `conflicts` table so a definitive 4xx on a
  /// client-originated row is never silent. The local cache row stays dirty (its
  /// `dirty=1` flag is the UI's "un-synced" signal); this surfaces WHY it can't
  /// sync. The conflict row's `server` value is null (the create never landed)
  /// and the `local` value carries the entity label + rejecting status for
  /// diagnosis.
  Future<void> _logCreateRejected(BudgetsOutboxItem item, int status) async {
    final p = item.payload;
    final String label;
    if (item.isExpense) {
      label = p['description']?.toString() ?? p['amount']?.toString() ?? '';
    } else if (item.isBudgetEntry) {
      label = p['source']?.toString() ?? p['amount']?.toString() ?? '';
    } else {
      label = p['name']?.toString() ?? '';
    }
    final descriptor = label.isEmpty ? item.entity : '${item.entity}: $label';
    await _dao.logConflict(
      id: item.entityId,
      field: 'create_rejected',
      local: 'HTTP $status — $descriptor',
      server: null, // create never landed → no server value
    );
    debugPrint(
      'BudgetsSync.push: CREATE rejected HTTP $status entity=${item.entity} '
      'id=${item.entityId} — conflict logged, cache stays dirty',
    );
  }

  Map<String, dynamic> _patchFrom(Map<String, dynamic> payload) {
    final out = Map<String, dynamic>.from(payload)..remove('id');
    return out;
  }

  /// Replace an item's payload (used for the coalesced update head).
  BudgetsOutboxItem _withPayload(
          BudgetsOutboxItem item, Map<String, dynamic> payload) =>
      BudgetsOutboxItem(
        seq: item.seq,
        op: item.op,
        entity: item.entity,
        entityId: item.entityId,
        payload: payload,
        createdAt: item.createdAt,
        attempts: item.attempts,
      );

  // ── update coalescing ─────────────────────────────────────────────────────

  /// Merge consecutive pending `update` ops for the same entity into one
  /// payload carried by the FIRST update row; the later rows are marked to be
  /// dequeued without a network call. Each merged head also carries the client
  /// `updated_at` (latest local edit time) so a server that stamps its own
  /// `updated_at` can't win LWW and revert a newer local edit. Both project and
  /// expense updates can appear in the budgets outbox; each coalesces on its own
  /// id (entity ids never collide across the two tables).
  _Coalesced _coalesceUpdates(List<BudgetsOutboxItem> queue) {
    // entityId → seq of the head update row that will carry the merged payload.
    final head = <String, int>{};
    // head seq → merged payload (mutable while folding).
    final merged = <int, Map<String, dynamic>>{};
    final skip = <int>{};

    for (final item in queue) {
      if (item.op != BudgetsOutboxOp.update) continue;
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
  String _lwwTimeFor(List<BudgetsOutboxItem> queue, int headSeq) {
    final headItem = queue.firstWhere((i) => i.seq == headSeq);
    var best = headItem.createdAt;
    for (final i in queue) {
      if (i.op == BudgetsOutboxOp.update &&
          i.entityId == headItem.entityId &&
          i.createdAt.compareTo(best) > 0) {
        best = i.createdAt;
      }
    }
    return best;
  }

  // ── PULL ────────────────────────────────────────────────────────────────

  /// Fetch the server delta and merge with last-write-wins. Advances the shared
  /// budgets cursor to the server's `now` on success. On a network failure it
  /// returns `pullFailed: true` and leaves the cursor untouched.
  Future<BudgetsSyncResult> pull() async {
    final cursor = await _dao.getCursor();
    debugPrint('BudgetsSync.pull: fetching changes since cursor=$cursor');
    BudgetChanges changes;
    try {
      changes = await _repo.fetchChanges(since: cursor);
    } catch (e) {
      debugPrint(
        'BudgetsSync.pull: fetchChanges failed — cursor unchanged: $e',
      );
      return BudgetsSyncResult(pullFailed: true, error: e);
    }

    var pulled = 0;
    var conflicts = 0;
    final nowIso = DateTime.now().toUtc().toIso8601String();

    // 1) Project upserts first (expenses reference projects).
    for (final sp in changes.projects) {
      final applied = await _mergeServerProject(sp, syncedAt: nowIso);
      if (applied.written) pulled++;
      if (applied.conflict) conflicts++;
    }

    // 2) Expense upserts next.
    for (final se in changes.expenses) {
      final applied = await _mergeServerExpense(se, syncedAt: nowIso);
      if (applied.written) pulled++;
      if (applied.conflict) conflicts++;
    }

    // 3) Budget-ledger upserts (top-ups). Also reference a project, so they
    //    follow the project upserts.
    for (final sbe in changes.budgetEntries) {
      final applied = await _mergeServerBudgetEntry(sbe, syncedAt: nowIso);
      if (applied.written) pulled++;
      if (applied.conflict) conflicts++;
    }

    var deletedApplied = 0;

    // 4) Project tombstones.
    for (final id in changes.deletedProjects) {
      final logged = await _applyServerProjectTombstone(id, syncedAt: nowIso);
      if (logged) conflicts++;
      deletedApplied++;
    }

    // 5) Expense tombstones.
    for (final id in changes.deletedExpenses) {
      final logged = await _applyServerExpenseTombstone(id, syncedAt: nowIso);
      if (logged) conflicts++;
      deletedApplied++;
    }

    // 6) Budget-ledger tombstones.
    for (final id in changes.deletedBudgetEntries) {
      final logged =
          await _applyServerBudgetEntryTombstone(id, syncedAt: nowIso);
      if (logged) conflicts++;
      deletedApplied++;
    }
    debugPrint(
      'BudgetsSync.pull: applied — merged=$pulled deleted=$deletedApplied '
      'conflicts=$conflicts',
    );

    // Advance the shared cursor only when the server gave us a real clock. An
    // empty `now` means we can't trust the page boundary — fall back to the max
    // `updated_at` we actually observed; if even that is empty, treat the pull
    // as FAILED so we don't silently skip the delta on the next run.
    final nextCursor = _resolveCursor(changes);
    if (nextCursor != null && nextCursor.isNotEmpty) {
      await _dao.setCursor(nextCursor);
      debugPrint('BudgetsSync.pull: cursor advanced → $nextCursor');
    } else {
      debugPrint(
        'BudgetsSync.pull: empty server clock with no datable rows — '
        'pull marked failed, cursor held',
      );
      return BudgetsSyncResult(
        pulled: pulled,
        deletedApplied: deletedApplied,
        conflicts: conflicts,
        pullFailed: true,
        error: StateError('server returned empty `now` with no datable rows'),
      );
    }

    return BudgetsSyncResult(
      pulled: pulled,
      deletedApplied: deletedApplied,
      conflicts: conflicts,
    );
  }

  /// The cursor to advance to: the server `now` when present, else the newest
  /// `updated_at` across the page's projects + expenses (so we never re-fetch
  /// from scratch on a server that omitted `now`). Null/empty → pull failure.
  String? _resolveCursor(BudgetChanges changes) {
    if (changes.now.isNotEmpty) return changes.now;
    String best = '';
    for (final sp in changes.projects) {
      final ua = sp.updatedAt ?? '';
      if (ua.isNotEmpty && ua.compareTo(best) > 0) best = ua;
    }
    for (final se in changes.expenses) {
      final ua = se.updatedAt ?? '';
      if (ua.isNotEmpty && ua.compareTo(best) > 0) best = ua;
    }
    for (final sbe in changes.budgetEntries) {
      final ua = sbe.updatedAt ?? '';
      if (ua.isNotEmpty && ua.compareTo(best) > 0) best = ua;
    }
    return best.isEmpty ? null : best;
  }

  // ── Project merge (LWW) ───────────────────────────────────────────────────

  Future<_MergeOutcome> _mergeServerProject(
    ServerProject sp, {
    required String syncedAt,
  }) {
    final serverProject = sp.project;
    final serverUpdatedAt = sp.updatedAt;

    return _dao.runInTransaction<_MergeOutcome>((txn) async {
      final localRow = await txn.getProjectRow(serverProject.id);

      if (localRow == null) {
        await txn.upsertProjectFromServer(
          serverProject,
          serverUpdatedAt: serverUpdatedAt,
          syncedAt: syncedAt,
        );
        return const _MergeOutcome(written: true, conflict: false);
      }

      final localDirty = ((localRow['dirty'] as int?) ?? 0) == 1;
      if (!localDirty) {
        await txn.upsertProjectFromServer(
          serverProject,
          serverUpdatedAt: serverUpdatedAt,
          syncedAt: syncedAt,
        );
        return const _MergeOutcome(written: true, conflict: false);
      }

      final localUpdatedAt = (localRow['updated_at'] as String?) ?? '';
      final serverWins = serverWinsByTime(serverUpdatedAt, localUpdatedAt);

      if (serverWins) {
        final logged = await _logProjectFieldConflicts(
            txn, localRow, serverProject, at: syncedAt);
        await txn.upsertProjectFromServer(
          serverProject,
          serverUpdatedAt: serverUpdatedAt,
          syncedAt: syncedAt,
        );
        return _MergeOutcome(written: true, conflict: logged);
      }

      // Local strictly newer — keep it; it re-pushes next sync. Nothing logged.
      debugPrint(
        'BudgetsSync.pull: kept newer local project id=${serverProject.id} — '
        'server change deferred, will re-push',
      );
      return const _MergeOutcome(written: false, conflict: false);
    });
  }

  Future<bool> _logProjectFieldConflicts(
    BudgetsTxn txn,
    Map<String, Object?> localRow,
    Project serverProject, {
    required String at,
  }) async {
    const fields = <String>[
      'name',
      'budget',
      'currency',
      'status',
      'description',
      'color',
      'is_favorite',
    ];
    final serverJson = serverProject.toJson();
    var any = false;
    for (final col in fields) {
      // Canonicalize so the local cache's INTEGER 0/1 for `is_favorite` never
      // reads as a conflict against the server model's JSON bool ("1" vs
      // "true"). Other fields pass through unchanged.
      final localVal = _canonField(col, localRow[col]);
      final serverVal = _canonField(col, serverJson[col]);
      if (localVal != serverVal) {
        any = true;
        debugPrint(
          'BudgetsSync.pull: LWW field conflict project id=${serverProject.id} '
          'field=$col winner=server',
        );
        await txn.logConflict(
          id: serverProject.id,
          field: col,
          local: localVal,
          server: serverVal,
          at: at,
        );
      }
    }
    return any;
  }

  /// Normalize a field value for cross-shape comparison. `is_favorite` is stored
  /// as INTEGER 0/1 locally but as a JSON bool on the server model, so reduce
  /// both to a canonical `'true'`/`'false'`. Everything else stringifies as-is.
  static String? _canonField(String col, Object? raw) {
    if (raw == null) return null;
    if (col == 'is_favorite') {
      final s = raw.toString().toLowerCase();
      return (s == '1' || s == 'true') ? 'true' : 'false';
    }
    return raw.toString();
  }

  // ── Expense merge (LWW) ────────────────────────────────────────────────────

  Future<_MergeOutcome> _mergeServerExpense(
    ServerExpense se, {
    required String syncedAt,
  }) {
    final serverExpense = se.expense;
    final serverUpdatedAt = se.updatedAt;

    return _dao.runInTransaction<_MergeOutcome>((txn) async {
      final localRow = await txn.getExpenseRow(serverExpense.id);

      if (localRow == null) {
        await txn.upsertExpenseFromServer(
          serverExpense,
          serverUpdatedAt: serverUpdatedAt,
          syncedAt: syncedAt,
        );
        return const _MergeOutcome(written: true, conflict: false);
      }

      final localDirty = ((localRow['dirty'] as int?) ?? 0) == 1;
      if (!localDirty) {
        await txn.upsertExpenseFromServer(
          serverExpense,
          serverUpdatedAt: serverUpdatedAt,
          syncedAt: syncedAt,
        );
        return const _MergeOutcome(written: true, conflict: false);
      }

      final localUpdatedAt = (localRow['updated_at'] as String?) ?? '';
      final serverWins = serverWinsByTime(serverUpdatedAt, localUpdatedAt);

      if (serverWins) {
        final logged = await _logExpenseFieldConflicts(
            txn, localRow, serverExpense, at: syncedAt);
        await txn.upsertExpenseFromServer(
          serverExpense,
          serverUpdatedAt: serverUpdatedAt,
          syncedAt: syncedAt,
        );
        return _MergeOutcome(written: true, conflict: logged);
      }

      debugPrint(
        'BudgetsSync.pull: kept newer local expense id=${serverExpense.id} — '
        'server change deferred, will re-push',
      );
      return const _MergeOutcome(written: false, conflict: false);
    });
  }

  Future<bool> _logExpenseFieldConflicts(
    BudgetsTxn txn,
    Map<String, Object?> localRow,
    Expense serverExpense, {
    required String at,
  }) async {
    const fields = <String>[
      'amount',
      'currency',
      'description',
      'vendor',
      'status',
      'is_favorite',
    ];
    final serverJson = serverExpense.toJson();
    var any = false;
    for (final col in fields) {
      // Canonicalize so the local cache's INTEGER 0/1 for `is_favorite` never
      // reads as a conflict against the server model's JSON bool ("1" vs
      // "true"). Other fields pass through unchanged (same helper the project
      // merge uses).
      final localVal = _canonField(col, localRow[col]);
      final serverVal = _canonField(col, serverJson[col]);
      if (localVal != serverVal) {
        any = true;
        debugPrint(
          'BudgetsSync.pull: LWW field conflict expense id=${serverExpense.id} '
          'field=$col winner=server',
        );
        await txn.logConflict(
          id: serverExpense.id,
          field: col,
          local: localVal,
          server: serverVal,
          at: at,
        );
      }
    }
    return any;
  }

  // ── Budget-ledger merge (LWW) ──────────────────────────────────────────────

  Future<_MergeOutcome> _mergeServerBudgetEntry(
    ServerBudgetEntry sbe, {
    required String syncedAt,
  }) {
    final serverEntry = sbe.entry;
    final serverUpdatedAt = sbe.updatedAt;

    return _dao.runInTransaction<_MergeOutcome>((txn) async {
      final localRow = await txn.getBudgetEntryRow(serverEntry.id);

      if (localRow == null) {
        await txn.upsertBudgetEntryFromServer(
          serverEntry,
          serverUpdatedAt: serverUpdatedAt,
          syncedAt: syncedAt,
        );
        return const _MergeOutcome(written: true, conflict: false);
      }

      final localDirty = ((localRow['dirty'] as int?) ?? 0) == 1;
      if (!localDirty) {
        await txn.upsertBudgetEntryFromServer(
          serverEntry,
          serverUpdatedAt: serverUpdatedAt,
          syncedAt: syncedAt,
        );
        return const _MergeOutcome(written: true, conflict: false);
      }

      final localUpdatedAt = (localRow['updated_at'] as String?) ?? '';
      final serverWins = serverWinsByTime(serverUpdatedAt, localUpdatedAt);

      if (serverWins) {
        final logged = await _logBudgetEntryFieldConflicts(
            txn, localRow, serverEntry, at: syncedAt);
        await txn.upsertBudgetEntryFromServer(
          serverEntry,
          serverUpdatedAt: serverUpdatedAt,
          syncedAt: syncedAt,
        );
        return _MergeOutcome(written: true, conflict: logged);
      }

      // Local strictly newer — keep it; it re-pushes next sync.
      debugPrint(
        'BudgetsSync.pull: kept newer local budget-entry id=${serverEntry.id} '
        '— server change deferred, will re-push',
      );
      return const _MergeOutcome(written: false, conflict: false);
    });
  }

  Future<bool> _logBudgetEntryFieldConflicts(
    BudgetsTxn txn,
    Map<String, Object?> localRow,
    BudgetEntry serverEntry, {
    required String at,
  }) async {
    const fields = <String>['amount', 'currency', 'source', 'kind'];
    final serverJson = serverEntry.toJson();
    var any = false;
    for (final col in fields) {
      final localVal = localRow[col]?.toString();
      final serverVal = serverJson[col]?.toString();
      if (localVal != serverVal) {
        any = true;
        debugPrint(
          'BudgetsSync.pull: LWW field conflict budget-entry '
          'id=${serverEntry.id} field=$col winner=server',
        );
        await txn.logConflict(
          id: serverEntry.id,
          field: col,
          local: localVal,
          server: serverVal,
          at: at,
        );
      }
    }
    return any;
  }

  // ── Tombstones (H1: server delete vs unsynced local edit) ──────────────────

  Future<bool> _applyServerProjectTombstone(String id,
      {required String syncedAt}) {
    return _dao.runInTransaction<bool>((txn) async {
      final localRow = await txn.getProjectRow(id);
      var loggedConflict = false;

      if (localRow != null) {
        final dirty = ((localRow['dirty'] as int?) ?? 0) == 1;
        final alreadyDeleted = ((localRow['deleted'] as int?) ?? 0) == 1;
        if (dirty && !alreadyDeleted) {
          await _logTombstoneConflict(
            txn,
            localRow,
            cols: const ['name', 'budget', 'description', 'status'],
            at: syncedAt,
          );
          loggedConflict = true;
        }
        if (dirty) {
          // The row is gone server-side; replaying its queued ops is pointless.
          await txn.deleteOutboxForEntity(kProjectEntity, id);
        }
      }

      // H1: cascade the project delete to its local child expenses in the SAME
      // transaction. The server delete cascades server-side but may not
      // enumerate every child in `deleted_expenses` — without this sweep those
      // children become permanent local orphans (their project is gone but they
      // still render). A CLEAN child mirrors the server cascade (deleted=1); a
      // DIRTY child has an un-pushed local edit the cascade is about to clobber,
      // so we log a conflict + reconcile its queued outbox op BEFORE deleting it
      // (delete-wins, never silent — same policy as the row's own tombstone).
      final childConflict = await _cascadeChildExpenses(txn, id, at: syncedAt);
      if (childConflict) loggedConflict = true;

      await txn.applyServerProjectDelete(id, syncedAt: syncedAt);
      return loggedConflict;
    });
  }

  /// Apply the server's project-delete cascade to every local child expense.
  /// Returns true when at least one DIRTY child logged a conflict.
  Future<bool> _cascadeChildExpenses(
    BudgetsTxn txn,
    String projectId, {
    required String at,
  }) async {
    final children = await txn.childExpenseRows(projectId);
    var loggedAny = false;
    for (final child in children) {
      final childId = (child['id'] as String?) ?? '';
      if (childId.isEmpty) continue;
      final dirty = ((child['dirty'] as int?) ?? 0) == 1;
      if (dirty) {
        // Un-pushed local edit on a child the project delete is clobbering — log
        // it + drop its now-moot queued op (the child is gone server-side).
        await _logTombstoneConflict(
          txn,
          child,
          cols: const ['amount', 'description', 'vendor', 'status'],
          at: at,
        );
        await txn.deleteOutboxForEntity(kExpenseEntity, childId);
        loggedAny = true;
      }
      // Apply the server cascade locally for both clean + reconciled-dirty rows.
      await txn.applyServerExpenseDelete(childId, syncedAt: at);
    }
    return loggedAny;
  }

  Future<bool> _applyServerExpenseTombstone(String id,
      {required String syncedAt}) {
    return _dao.runInTransaction<bool>((txn) async {
      final localRow = await txn.getExpenseRow(id);
      var loggedConflict = false;

      if (localRow != null) {
        final dirty = ((localRow['dirty'] as int?) ?? 0) == 1;
        final alreadyDeleted = ((localRow['deleted'] as int?) ?? 0) == 1;
        if (dirty && !alreadyDeleted) {
          await _logTombstoneConflict(
            txn,
            localRow,
            cols: const ['amount', 'description', 'vendor', 'status'],
            at: syncedAt,
          );
          loggedConflict = true;
        }
        if (dirty) {
          await txn.deleteOutboxForEntity(kExpenseEntity, id);
        }
      }

      await txn.applyServerExpenseDelete(id, syncedAt: syncedAt);
      return loggedConflict;
    });
  }

  Future<bool> _applyServerBudgetEntryTombstone(String id,
      {required String syncedAt}) {
    return _dao.runInTransaction<bool>((txn) async {
      final localRow = await txn.getBudgetEntryRow(id);
      var loggedConflict = false;

      if (localRow != null) {
        final dirty = ((localRow['dirty'] as int?) ?? 0) == 1;
        final alreadyDeleted = ((localRow['deleted'] as int?) ?? 0) == 1;
        if (dirty && !alreadyDeleted) {
          await _logTombstoneConflict(
            txn,
            localRow,
            cols: const ['amount', 'currency', 'source', 'kind'],
            at: syncedAt,
          );
          loggedConflict = true;
        }
        if (dirty) {
          await txn.deleteOutboxForEntity(kBudgetEntryEntity, id);
        }
      }

      await txn.applyServerBudgetEntryDelete(id, syncedAt: syncedAt);
      return loggedConflict;
    });
  }

  /// Record each non-empty local field as a conflict against the server delete
  /// (server value is null — the row no longer exists).
  Future<void> _logTombstoneConflict(
    BudgetsTxn txn,
    Map<String, Object?> localRow, {
    required List<String> cols,
    required String at,
  }) async {
    final id = (localRow['id'] as String?) ?? '';
    for (final col in cols) {
      final localVal = localRow[col]?.toString();
      if (localVal == null || localVal.isEmpty) continue;
      debugPrint(
        'BudgetsSync.pull: tombstone conflict id=$id field=$col '
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

  // ── helpers ──────────────────────────────────────────────────────────────

  static double? _asDouble(Object? v) {
    if (v == null) return null;
    if (v is double) return v;
    if (v is int) return v.toDouble();
    return double.tryParse(v.toString());
  }

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
