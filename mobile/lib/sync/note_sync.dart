import 'package:dio/dio.dart';

import '../core/api/api_exceptions.dart';
import '../local/note_dao.dart';
import '../models/note.dart';
import '../repositories/notes_repository.dart';

/// Raised when a push stops early because the network/server is unreachable, or
/// because a retryable server error (5xx) should keep the queue intact. The
/// drained-so-far items are already removed from the outbox; the rest stay
/// queued for the next sync.
class _PushInterrupted implements Exception {
  final Object cause;
  _PushInterrupted(this.cause);
}

/// Outcome of one [NoteSync.sync] run — handy for tests + UI diagnostics.
class NoteSyncResult {
  final int pushed;
  final int pulled;
  final int deletedApplied;
  final int conflicts;
  final bool pushInterrupted;
  final bool pullFailed;
  final Object? error;

  const NoteSyncResult({
    this.pushed = 0,
    this.pulled = 0,
    this.deletedApplied = 0,
    this.conflicts = 0,
    this.pushInterrupted = false,
    this.pullFailed = false,
    this.error,
  });
}

/// The offline-first sync engine for notes.
///
/// A one-for-one mirror of `task_sync.dart` adapted to the Notes domain: there
/// is NO `complete` op (only create / update / delete), and conflict logging
/// covers the note fields (title / content / importance / pinned).
///
/// * [push] drains the outbox in order, calling the matching
///   `/api/lazybrain/notes*` endpoint. On a network OR retryable-server failure
///   it STOPS (the failed item stays queued); only a definitive 4xx is allowed
///   to drain.
/// * [pull] fetches `GET /api/lazybrain/notes/changes?since=<cursor>` and merges
///   with last-write-wins by `updated_at`; the loser of a real both-sides change
///   is recorded in `conflicts` (never silently dropped).
/// * [sync] = push() then pull(), guarded against concurrent runs.
class NoteSync {
  final NoteDao _dao;
  final NotesRepository _repo;

  /// A retryable (5xx) item is dead-lettered after this many failed attempts so
  /// one poison row can't wedge the whole queue forever.
  static const int kMaxPushAttempts = 5;

  bool _running = false;

  NoteSync(this._dao, this._repo);

  bool get isRunning => _running;

  /// push() then pull(). A second call while one is in flight is a no-op and
  /// returns an empty result.
  Future<NoteSyncResult> sync() async {
    if (_running) return const NoteSyncResult();
    _running = true;
    try {
      final pushResult = await push();
      final pullResult = await pull();
      return NoteSyncResult(
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
  Future<NoteSyncResult> push() async {
    final queue = await _dao.readOutbox();
    // Coalesce consecutive `update` ops per entity so replays can't interleave
    // with server-stamped times. The first update row of a run keeps the merged
    // payload; the rest are no-op'd (just dequeued).
    final coalesced = _coalesceUpdates(queue);

    var pushed = 0;
    for (final item in queue) {
      try {
        // Skipped duplicate update rows: just dequeue, no network call.
        if (coalesced.skipSeqs.contains(item.seq)) {
          await _dao.deleteOutboxItem(item.seq);
          continue;
        }
        final effective = coalesced.payloads[item.seq] != null
            ? _withPayload(item, coalesced.payloads[item.seq]!)
            : item;

        final committed = await _pushOne(effective);
        if (committed) {
          // Retire the pushed item atomically (delete outbox row + clear dirty /
          // hard-remove tombstone) so a crash can't split the two writes.
          await _dao.commitPush(item.seq, item.entityId);
          pushed++;
        }
        // A drained-but-not-committed item (definitive 4xx, or a dead-lettered
        // 5xx poison) has already had its outbox row removed inside the failure
        // classifier; we leave its cache row dirty so the NEXT pull restores
        // server truth — never silently dropping the user's edit.
      } on _PushInterrupted catch (e) {
        // Network down OR a retryable server error — stop, keep the rest queued.
        return NoteSyncResult(
          pushed: pushed,
          pushInterrupted: true,
          error: e.cause,
        );
      }
    }
    return NoteSyncResult(pushed: pushed);
  }

  /// Push one queued op. Returns true when the server accepted it (so the
  /// caller commits the retire); false when the item was DRAINED on a definitive
  /// client error or dead-lettered as a 5xx poison (the failure classifier has
  /// already removed its outbox row, and the cache stays dirty for the next
  /// pull). Throws [_PushInterrupted] to STOP the drain and keep the queue.
  Future<bool> _pushOne(NoteOutboxItem item) async {
    final p = item.payload;
    try {
      switch (item.op) {
        case NoteOutboxOp.create:
          await _repo.createNote(
            content: (p['content'] ?? '').toString(),
            id: p['id']?.toString() ?? item.entityId,
            title: p['title']?.toString(),
            tags: _tagsFrom(p['tags']),
            importance: _intOrNull(p['importance']),
            pinned: _boolOrNull(p['pinned']),
          );
          break;
        case NoteOutboxOp.update:
          await _repo.updateNotePatch(item.entityId, _patchFrom(p));
          break;
        case NoteOutboxOp.delete:
          await _repo.deleteNote(item.entityId);
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
  /// queued edit on a transient failure. Returns `true` ONLY for an idempotent
  /// 404-on-delete (treated as a success → caller commits + counts it). For the
  /// other drain branches it removes THIS item's outbox row here and returns
  /// `false`, leaving the cache row dirty so the next pull re-establishes server
  /// truth. Throws [_PushInterrupted] to STOP the drain and keep the queue.
  ///   * network (timeout/connection/cancel, status 0, non-badResponse) →
  ///     [_PushInterrupted] (stop draining, keep ALL queued items);
  ///   * server 5xx → retryable: bump the attempt counter, dead-letter after
  ///     [kMaxPushAttempts], otherwise [_PushInterrupted] (keep it queued);
  ///   * 404 on delete → idempotent success (return true → caller commits);
  ///   * other 4xx → drain the outbox row (return false; next pull restores it).
  Future<bool> _classifyPushFailure(NoteOutboxItem item, Object e) async {
    if (_isNetworkError(e)) {
      throw _PushInterrupted(e);
    }
    final api = _asApiError(e);
    final status = api?.status ?? _statusOf(e) ?? 0;

    // 404 on delete is idempotent success — the row is already gone server-side.
    // Return true so the caller commits (removes the outbox row AND hard-removes
    // the local tombstone) and counts it as pushed.
    if (status == 404 && item.op == NoteOutboxOp.delete) {
      return true;
    }

    if (status >= 500) {
      // Retryable. Count the attempt; dead-letter a poison item, else stop and
      // keep it queued for the next sync — NEVER silently drop.
      final attempts = await _dao.bumpOutboxAttempts(item.seq);
      if (attempts >= kMaxPushAttempts) {
        await _dao.deadLetterOutboxItem(item.seq);
        return false; // drained the poison item; local stays dirty for next pull
      }
      throw _PushInterrupted(e);
    }

    if (status >= 400) {
      // Definitive client error (validation/conflict). For an update/delete this
      // is safe to drain silently — the row exists server-side, so the next pull
      // restores server truth. But a CREATE is client-originated: there is NO
      // server truth to restore, so silently dropping the only queued record
      // permanently strands the dirty cache row (an orphan the user can never
      // re-sync). Log it as a visible conflict BEFORE draining so the UI / next
      // sync can surface the rejection.
      if (item.op == NoteOutboxOp.create) {
        await _logCreateRejected(item, status);
      }
      await _dao.deleteOutboxItem(item.seq);
      return false; // drained
    }

    // Unknown shape with no usable status → treat as network-ish and keep it.
    throw _PushInterrupted(e);
  }

  /// Record a rejected CREATE in the `conflicts` table so a definitive 4xx on a
  /// client-originated row is never silent. The local cache row stays dirty (its
  /// `dirty=1` flag is the UI's "un-synced" signal); this surfaces WHY it can't
  /// sync. The conflict row's `server` value is null (the create never landed)
  /// and the `local` value carries the entity label + rejecting status for
  /// diagnosis.
  Future<void> _logCreateRejected(NoteOutboxItem item, int status) async {
    final p = item.payload;
    final label = p['title']?.toString() ?? p['content']?.toString() ?? '';
    final descriptor = label.isEmpty ? item.entity : '${item.entity}: $label';
    await _dao.logConflict(
      id: item.entityId,
      field: 'create_rejected',
      local: 'HTTP $status — $descriptor',
      server: null, // create never landed → no server value
    );
  }

  Map<String, dynamic> _patchFrom(Map<String, dynamic> payload) {
    final out = Map<String, dynamic>.from(payload)..remove('id');
    return out;
  }

  /// Replace an item's payload (used for the coalesced update head).
  NoteOutboxItem _withPayload(NoteOutboxItem item, Map<String, dynamic> payload) =>
      NoteOutboxItem(
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
  Future<NoteSyncResult> pull() async {
    final cursor = await _dao.getCursor();
    NoteChanges changes;
    try {
      changes = await _repo.fetchChanges(since: cursor);
    } catch (e) {
      return NoteSyncResult(pullFailed: true, error: e);
    }

    var pulled = 0;
    var conflicts = 0;
    final nowIso = DateTime.now().toUtc().toIso8601String();

    for (final sn in changes.notes) {
      final applied = await _mergeServerNote(sn, syncedAt: nowIso);
      if (applied.written) pulled++;
      if (applied.conflict) conflicts++;
    }

    var deletedApplied = 0;
    for (final id in changes.deleted) {
      final logged = await _applyServerTombstone(id, syncedAt: nowIso);
      if (logged) conflicts++;
      deletedApplied++;
    }

    // Advance the cursor only when the server gave us a real clock. An empty
    // `now` means we can't trust the page boundary — fall back to the max
    // `updated_at` we actually observed; if even that is empty, treat the pull
    // as FAILED so we don't silently skip the delta on the next run.
    final nextCursor = _resolveCursor(changes);
    if (nextCursor != null && nextCursor.isNotEmpty) {
      await _dao.setCursor(nextCursor);
    } else {
      return NoteSyncResult(
        pulled: pulled,
        deletedApplied: deletedApplied,
        conflicts: conflicts,
        pullFailed: true,
        error: StateError('server returned empty `now` with no datable rows'),
      );
    }

    return NoteSyncResult(
      pulled: pulled,
      deletedApplied: deletedApplied,
      conflicts: conflicts,
    );
  }

  /// The cursor to advance to: the server `now` when present, else the newest
  /// `updated_at` across the page's notes (so we never re-fetch from scratch on
  /// a server that omitted `now`). Null/empty → caller treats as a pull failure.
  String? _resolveCursor(NoteChanges changes) {
    if (changes.now.isNotEmpty) return changes.now;
    String best = '';
    for (final sn in changes.notes) {
      final ua = sn.updatedAt ?? '';
      if (ua.isNotEmpty && ua.compareTo(best) > 0) best = ua;
    }
    return best.isEmpty ? null : best;
  }

  /// Apply a server tombstone with safety: if the local row has an UNSYNCED
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
    NoteTxn txn,
    Map<String, Object?> localRow, {
    required String at,
  }) async {
    final id = (localRow['id'] as String?) ?? '';
    for (final col in _conflictFields) {
      final localVal = localRow[col]?.toString();
      if (localVal == null || localVal.isEmpty) continue;
      await txn.logConflict(
        id: id,
        field: col,
        local: localVal,
        server: null, // server-deleted → no server value
        at: at,
      );
    }
  }

  /// Apply last-write-wins for a single server note against the local cache.
  ///
  /// The dirty-check + the upsert run in ONE transaction so a concurrent local
  /// write can't land between the read and the server-write.
  ///
  /// * No local row → write the server copy.
  /// * Local NOT dirty → server wins (write).
  /// * Local dirty:
  ///     - server `updated_at` >= local `updated_at` → server wins, and the
  ///       overwritten local edit is logged to `conflicts` (both sides changed).
  ///       Spurious "the server just echoed my own push" diffs are NOT logged.
  ///     - local strictly newer → keep local (don't clobber the user's
  ///       un-pushed edit; it will push on the next sync). No log needed.
  Future<_MergeOutcome> _mergeServerNote(
    ServerNote sn, {
    required String syncedAt,
  }) {
    final serverNote = sn.note;
    final serverUpdatedAt = sn.updatedAt;

    return _dao.runInTransaction<_MergeOutcome>((txn) async {
      final localRow = await txn.getRow(serverNote.id);

      if (localRow == null) {
        await txn.upsertFromServer(
          serverNote,
          serverUpdatedAt: serverUpdatedAt,
          syncedAt: syncedAt,
        );
        return const _MergeOutcome(written: true, conflict: false);
      }

      final localDirty = ((localRow['dirty'] as int?) ?? 0) == 1;
      if (!localDirty) {
        await txn.upsertFromServer(
          serverNote,
          serverUpdatedAt: serverUpdatedAt,
          syncedAt: syncedAt,
        );
        return const _MergeOutcome(written: true, conflict: false);
      }

      // Local is dirty — a genuine concurrent edit. Compare timestamps.
      final localUpdatedAt = (localRow['updated_at'] as String?) ?? '';
      final serverWins = _gte(serverUpdatedAt, localUpdatedAt);

      if (serverWins) {
        // Server wins; log the local edit we are about to overwrite. Only real
        // divergences are logged (a server row that merely echoes our just-
        // pushed values produces no diff → no spurious conflict).
        final logged =
            await _logFieldConflicts(txn, localRow, serverNote, at: syncedAt);
        await txn.upsertFromServer(
          serverNote,
          serverUpdatedAt: serverUpdatedAt,
          syncedAt: syncedAt,
        );
        return _MergeOutcome(written: true, conflict: logged);
      }

      // Local strictly newer — keep it; it re-pushes next sync. Nothing logged.
      return const _MergeOutcome(written: false, conflict: false);
    });
  }

  /// Record each differing field as a conflict row so the loser is never lost.
  /// Returns true when at least one field actually differed (so a server echo
  /// of our own push logs nothing).
  Future<bool> _logFieldConflicts(
    NoteTxn txn,
    Map<String, Object?> localRow,
    Note serverNote, {
    required String at,
  }) async {
    final serverVals = _serverFieldValues(serverNote);
    var any = false;
    for (final col in _conflictFields) {
      final localVal = localRow[col]?.toString();
      final serverVal = serverVals[col];
      if (localVal != serverVal) {
        any = true;
        await txn.logConflict(
          id: serverNote.id,
          field: col,
          local: localVal,
          server: serverVal,
          at: at,
        );
      }
    }
    return any;
  }

  /// The set of fields compared for conflicts. `tags` is intentionally excluded
  /// here because the cache stores it JSON-encoded while the model carries a
  /// list — the meaningful user-facing divergences are the scalar fields.
  static const List<String> _conflictFields = <String>[
    'title',
    'content',
    'importance',
    'pinned',
  ];

  /// Render a server [Note]'s conflict-relevant fields as the SAME string shape
  /// the cache row stores, so equality comparisons are apples-to-apples
  /// (`pinned` is `'1'`/`'0'`, `importance` is the int's string form).
  static Map<String, String?> _serverFieldValues(Note n) => {
        'title': n.title,
        'content': n.content,
        'importance': n.importance.toString(),
        'pinned': n.pinned ? '1' : '0',
      };

  // ── update coalescing ─────────────────────────────────────────────────────

  /// Merge consecutive pending `update` ops for the same entity into one
  /// payload carried by the FIRST update row; the later rows are marked to be
  /// dequeued without a network call. Each merged head also carries the client
  /// `updated_at` (latest local edit time) so the server can honor client LWW.
  _Coalesced _coalesceUpdates(List<NoteOutboxItem> queue) {
    // entityId → seq of the head update row that will carry the merged payload.
    final head = <String, int>{};
    // head seq → merged payload (mutable while folding).
    final merged = <int, Map<String, dynamic>>{};
    final skip = <int>{};

    for (final item in queue) {
      if (item.op != NoteOutboxOp.update) continue;
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
  String _lwwTimeFor(List<NoteOutboxItem> queue, int headSeq) {
    final headItem = queue.firstWhere((i) => i.seq == headSeq);
    var best = headItem.createdAt;
    for (final i in queue) {
      if (i.op == NoteOutboxOp.update &&
          i.entityId == headItem.entityId &&
          i.createdAt.compareTo(best) > 0) {
        best = i.createdAt;
      }
    }
    return best;
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

  /// Coerce a payload `tags` value into a `List<String>?`.
  static List<String>? _tagsFrom(dynamic v) {
    if (v == null) return null;
    if (v is List) return v.map((e) => e.toString()).toList();
    return null;
  }

  static int? _intOrNull(dynamic v) {
    if (v == null) return null;
    if (v is int) return v;
    if (v is double) return v.toInt();
    return int.tryParse(v.toString());
  }

  static bool? _boolOrNull(dynamic v) {
    if (v == null) return null;
    if (v is bool) return v;
    if (v is int) return v != 0;
    if (v is String) return v == 'true' || v == '1';
    return null;
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
