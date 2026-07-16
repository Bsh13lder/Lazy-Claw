import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';

import '../core/api/api_exceptions.dart';
import '../local/document_cache_dao.dart';
import '../repositories/documents_repository.dart';

/// Raised when a push stops early because the network/server is unreachable, or
/// because a retryable server error (5xx) should keep the queue intact.
class _PushInterrupted implements Exception {
  final Object cause;
  _PushInterrupted(this.cause);
}

/// Outcome of one [DocumentSync.sync] run — handy for tests + UI diagnostics.
class DocSyncResult {
  final int pushed;
  final int pulled;
  final int deletedApplied;
  final int conflicts;
  final bool pushInterrupted;
  final bool pullFailed;
  final Object? error;

  const DocSyncResult({
    this.pushed = 0,
    this.pulled = 0,
    this.deletedApplied = 0,
    this.conflicts = 0,
    this.pushInterrupted = false,
    this.pullFailed = false,
    this.error,
  });
}

/// The offline-first sync engine for the office suite (Sheets / Docs / PDF).
///
/// A mirror of `note_sync.dart` adapted to documents:
///   * PUSH drains the shared `outbox` (document entity) in seq order. Each item
///     calls the matching `DocumentsRepository` verb: create-with-id (POST),
///     save (PUT, with `base_updated_at` for CAS → 409 conflict), or delete
///     (DELETE soft). On a network OR retryable-5xx failure it STOPS (the failed
///     item stays queued); a definitive 4xx drains; a 409 on save is recorded as
///     a conflict and drained (the next pull restores server truth) — never lost.
///   * PULL fetches `GET /api/<kind>/changes?since=<cursor>` and merges with
///     last-write-wins by `updated_at`; the loser of a both-sides change is
///     logged in `conflicts` (never silently dropped). Tombstones apply
///     delete-wins, logging a conflict when they clobber an unsynced local edit.
///   * sync() = push() then pull(), guarded against concurrent runs.
///
/// PDFs are import-only: their content is immutable server-side, so the engine
/// only ever PUSHes a `delete` for them (a stray create/update is dropped), but
/// their metadata + tombstones flow through `/changes` so a web/agent delete or
/// rename propagates to mobile.
class DocumentSync {
  final DocumentCacheDao _dao;
  final DocumentsRepository _repo;
  final DocKind _kind;

  /// A retryable (5xx) item is dead-lettered after this many failed attempts so
  /// one poison row can't wedge the whole queue forever.
  static const int kMaxPushAttempts = 5;

  bool _running = false;

  DocumentSync(this._dao, this._repo, this._kind);

  bool get isRunning => _running;
  DocKind get kind => _kind;

  /// push() then pull(). A second call while one is in flight is a no-op.
  Future<DocSyncResult> sync() async {
    if (_running) return const DocSyncResult();
    _running = true;
    try {
      final pushResult = await push();
      final pullResult = await pull();
      return DocSyncResult(
        pushed: pushResult.pushed,
        pulled: pullResult.pulled,
        deletedApplied: pullResult.deletedApplied,
        conflicts: pushResult.conflicts + pullResult.conflicts,
        pushInterrupted: pushResult.pushInterrupted,
        pullFailed: pullResult.pullFailed,
        error: pushResult.error ?? pullResult.error,
      );
    } finally {
      _running = false;
    }
  }

  // ── PUSH ────────────────────────────────────────────────────────────────

  /// Drain the document outbox in seq order, filtered to THIS kind. Returns how
  /// many items were pushed. On a network OR retryable-server failure it stops
  /// early (remaining items retried next sync). A definitive 4xx is safe to drain.
  Future<DocSyncResult> push() async {
    final all = await _dao.readOutbox();
    final queue = all.where((i) => i.kind == _kind.api).toList();
    // Coalesce consecutive `update` ops per entity so replays can't interleave.
    final coalesced = _coalesceUpdates(queue);
    if (queue.isNotEmpty) {
      debugPrint(
        'DocumentSync[${_kind.api}].push: draining outbox — ${queue.length} '
        'op(s), ${coalesced.skipSeqs.length} coalesced',
      );
    }

    var pushed = 0;
    var conflicts = 0;
    for (final item in queue) {
      try {
        if (coalesced.skipSeqs.contains(item.seq)) {
          debugPrint(
            'DocumentSync[${_kind.api}].push: coalesced-skip seq=${item.seq} '
            'op=${item.op} id=${item.docId}',
          );
          await _dao.deleteOutboxItem(item.seq);
          continue;
        }
        final effective = coalesced.payloads[item.seq] != null
            ? _withPayload(item, coalesced.payloads[item.seq]!)
            : item;

        final outcome = await _pushOne(effective);
        if (outcome.committed) {
          await _dao.commitPush(item.seq, item.entityId,
              serverUpdatedAt: outcome.serverUpdatedAt);
          pushed++;
          debugPrint(
            'DocumentSync[${_kind.api}].push: pushed seq=${item.seq} '
            'op=${item.op} id=${item.docId}',
          );
        }
        if (outcome.conflictLogged) conflicts++;
      } on _PushInterrupted catch (e) {
        debugPrint(
          'DocumentSync[${_kind.api}].push: interrupted after $pushed pushed — '
          'remaining outbox op(s) preserved for next sync: ${e.cause}',
        );
        return DocSyncResult(
          pushed: pushed,
          conflicts: conflicts,
          pushInterrupted: true,
          error: e.cause,
        );
      }
    }
    if (queue.isNotEmpty) {
      debugPrint(
        'DocumentSync[${_kind.api}].push: drain complete — $pushed pushed, '
        '$conflicts conflict(s)',
      );
    }
    return DocSyncResult(pushed: pushed, conflicts: conflicts);
  }

  /// Push one queued op. Returns a [_PushOutcome] describing whether the caller
  /// should commit the retire (and the server's returned updated_at), and
  /// whether a conflict was logged. Throws [_PushInterrupted] to STOP the drain.
  Future<_PushOutcome> _pushOne(DocOutboxItem item) async {
    final p = item.payload;
    final isPdf = item.kind == DocKind.pdf.api;
    try {
      switch (item.op) {
        case DocOutboxOp.create:
          // PDFs are import-only; a create we can't replay is dropped silently
          // (the next pull restores any real server row).
          if (isPdf) return const _PushOutcome(committed: true);
          final kind = _kindFromApi(item.kind);
          final meta = await _repo.create(
            kind,
            p['name']?.toString() ?? 'Untitled',
            id: p['id']?.toString() ?? item.docId,
          );
          // If we have a queued payload, persist it too (create makes a blank).
          final payloadJson = p['payload']?.toString();
          if (payloadJson != null && payloadJson.isNotEmpty) {
            final at = await _repo.save(
              kind,
              item.docId,
              _decodePayload(payloadJson),
              baseUpdatedAt: meta.updatedAt,
            );
            return _PushOutcome(committed: true, serverUpdatedAt: at);
          }
          return _PushOutcome(committed: true, serverUpdatedAt: meta.updatedAt);

        case DocOutboxOp.update:
          if (isPdf) return const _PushOutcome(committed: true);
          final kind = _kindFromApi(item.kind);
          final payloadJson = p['payload']?.toString();
          if (payloadJson == null || payloadJson.isEmpty) {
            return const _PushOutcome(committed: true);
          }
          final at = await _repo.save(
            kind,
            item.docId,
            _decodePayload(payloadJson),
            baseUpdatedAt: p['base_updated_at']?.toString(),
          );
          return _PushOutcome(committed: true, serverUpdatedAt: at);

        case DocOutboxOp.delete:
          await _repo.delete(_kindFromApi(item.kind), item.docId);
          return const _PushOutcome(committed: true);

        default:
          return const _PushOutcome(committed: true);
      }
    } on DocConflictException catch (e) {
      // A 409 on save: the server changed under us. Log it (never lose the
      // user's edit), drain the outbox row, and let the next pull restore server
      // truth (which carries the conflict's server snapshot).
      debugPrint(
        'DocumentSync[${_kind.api}].push: 409 save conflict id=${item.docId} '
        'field=save_conflict winner=server — draining, next pull restores truth',
      );
      await _dao.logConflict(
        kind: item.kind,
        id: item.docId,
        field: 'save_conflict',
        local: 'local edit superseded server version '
            '${e.current.updatedAt ?? ''}',
        server: e.current.updatedAt,
      );
      await _dao.deleteOutboxItem(item.seq);
      return const _PushOutcome(committed: false, conflictLogged: true);
    } catch (e) {
      return _classifyPushFailure(item, e);
    }
  }

  /// Decide what a push failure means and act on it. NEVER silently drops a
  /// queued edit on a transient failure.
  ///   * network → [_PushInterrupted] (stop, keep ALL queued items);
  ///   * 5xx → retryable: bump attempt counter, dead-letter after N, else stop;
  ///   * 404 on delete → idempotent success (commit);
  ///   * other 4xx → drain the row (next pull restores it); a create 4xx logs a
  ///     visible conflict first so a client-originated row isn't silently lost.
  Future<_PushOutcome> _classifyPushFailure(DocOutboxItem item, Object e) async {
    if (_isNetworkError(e)) {
      debugPrint(
        'DocumentSync[${_kind.api}].push: network/transport failure '
        'op=${item.op} id=${item.docId} — stop drain, keep queue: $e',
      );
      throw _PushInterrupted(e);
    }
    final status = _statusOf(e) ?? 0;

    if (status == 404 && item.op == DocOutboxOp.delete) {
      debugPrint(
        'DocumentSync[${_kind.api}].push: HTTP 404 on delete id=${item.docId} '
        '— idempotent success',
      );
      return const _PushOutcome(committed: true);
    }

    if (status >= 500) {
      final attempts = await _dao.bumpOutboxAttempts(item.seq);
      if (attempts >= kMaxPushAttempts) {
        debugPrint(
          'DocumentSync[${_kind.api}].push: dead-lettered op=${item.op} '
          'id=${item.docId} after $attempts attempt(s) (HTTP $status)',
        );
        await _dao.deadLetterOutboxItem(item.seq);
        return const _PushOutcome(committed: false);
      }
      debugPrint(
        'DocumentSync[${_kind.api}].push: retryable HTTP $status op=${item.op} '
        'id=${item.docId} attempt=$attempts — keeping queued',
      );
      throw _PushInterrupted(e);
    }

    if (status >= 400) {
      if (item.op == DocOutboxOp.create) {
        await _logCreateRejected(item, status);
      } else {
        debugPrint(
          'DocumentSync[${_kind.api}].push: definitive HTTP $status '
          'op=${item.op} id=${item.docId} — draining outbox row',
        );
      }
      await _dao.deleteOutboxItem(item.seq);
      return const _PushOutcome(committed: false);
    }

    // Unknown shape with no usable status → treat as network-ish; keep queued.
    debugPrint(
      'DocumentSync[${_kind.api}].push: unclassifiable failure op=${item.op} '
      'id=${item.docId} — keeping queued: $e',
    );
    throw _PushInterrupted(e);
  }

  Future<void> _logCreateRejected(DocOutboxItem item, int status) async {
    final label = item.payload['name']?.toString() ?? '';
    final descriptor = label.isEmpty ? item.kind : '${item.kind}: $label';
    await _dao.logConflict(
      kind: item.kind,
      id: item.docId,
      field: 'create_rejected',
      local: 'HTTP $status — $descriptor',
      server: null,
    );
    debugPrint(
      'DocumentSync[${_kind.api}].push: CREATE rejected HTTP $status '
      'id=${item.docId} — conflict logged, cache stays dirty',
    );
  }

  DocOutboxItem _withPayload(DocOutboxItem item, Map<String, dynamic> payload) =>
      DocOutboxItem(
        seq: item.seq,
        op: item.op,
        entityId: item.entityId,
        payload: payload,
        createdAt: item.createdAt,
        attempts: item.attempts,
      );

  // ── PULL ────────────────────────────────────────────────────────────────

  /// Fetch the server delta and merge with last-write-wins. Advances the cursor
  /// to the server's `now` on success.
  Future<DocSyncResult> pull() async {
    final cursor = await _dao.getCursor(_kind.api);
    debugPrint(
      'DocumentSync[${_kind.api}].pull: fetching changes since cursor=$cursor',
    );
    DocChanges changes;
    try {
      changes = await _repo.fetchChanges(_kind, since: cursor);
    } catch (e) {
      debugPrint(
        'DocumentSync[${_kind.api}].pull: fetchChanges failed — cursor '
        'unchanged: $e',
      );
      return DocSyncResult(pullFailed: true, error: e);
    }

    var pulled = 0;
    var conflicts = 0;
    final nowIso = DateTime.now().toUtc().toIso8601String();

    for (final sd in changes.docs) {
      final applied = await _mergeServerDoc(sd, syncedAt: nowIso);
      if (applied.written) pulled++;
      if (applied.conflict) conflicts++;
    }

    var deletedApplied = 0;
    for (final id in changes.deleted) {
      final logged = await _applyServerTombstone(id, syncedAt: nowIso);
      if (logged) conflicts++;
      deletedApplied++;
    }
    debugPrint(
      'DocumentSync[${_kind.api}].pull: applied — merged=$pulled '
      'deleted=$deletedApplied conflicts=$conflicts',
    );

    final nextCursor = _resolveCursor(changes);
    if (nextCursor != null && nextCursor.isNotEmpty) {
      await _dao.setCursor(_kind.api, nextCursor);
      debugPrint(
        'DocumentSync[${_kind.api}].pull: cursor advanced → $nextCursor',
      );
    } else {
      debugPrint(
        'DocumentSync[${_kind.api}].pull: empty server clock with no datable '
        'rows — pull marked failed, cursor held',
      );
      return DocSyncResult(
        pulled: pulled,
        deletedApplied: deletedApplied,
        conflicts: conflicts,
        pullFailed: true,
        error: StateError('server returned empty server_time with no datable rows'),
      );
    }

    return DocSyncResult(
      pulled: pulled,
      deletedApplied: deletedApplied,
      conflicts: conflicts,
    );
  }

  String? _resolveCursor(DocChanges changes) {
    if (changes.now.isNotEmpty) return changes.now;
    String best = '';
    for (final sd in changes.docs) {
      final ua = sd.updatedAt ?? '';
      if (ua.isNotEmpty && ua.compareTo(best) > 0) best = ua;
    }
    return best.isEmpty ? null : best;
  }

  /// Apply last-write-wins for a single server doc against the local cache.
  Future<_MergeOutcome> _mergeServerDoc(ServerDoc sd, {required String syncedAt}) {
    return _dao.runInTransaction<_MergeOutcome>((txn) async {
      final localRow = await txn.getRow(_kind.api, sd.id);

      if (localRow == null) {
        await txn.upsertFromServer(
          kind: _kind.api,
          id: sd.id,
          name: sd.name,
          payloadJson: sd.payloadJson,
          serverUpdatedAt: sd.updatedAt,
          syncedAt: syncedAt,
        );
        return const _MergeOutcome(written: true, conflict: false);
      }

      final localDirty = ((localRow['dirty'] as int?) ?? 0) == 1;
      if (!localDirty) {
        await txn.upsertFromServer(
          kind: _kind.api,
          id: sd.id,
          name: sd.name,
          payloadJson: sd.payloadJson,
          serverUpdatedAt: sd.updatedAt,
          syncedAt: syncedAt,
        );
        return const _MergeOutcome(written: true, conflict: false);
      }

      // Local is dirty — a genuine concurrent edit. Compare timestamps.
      final localUpdatedAt = (localRow['updated_at'] as String?) ?? '';
      final serverWins = _gte(sd.updatedAt, localUpdatedAt);

      if (serverWins) {
        final localPayload = localRow['payload']?.toString();
        final logged = (localPayload != null &&
                localPayload.isNotEmpty &&
                localPayload != (sd.payloadJson ?? ''))
            ? true
            : false;
        if (logged) {
          debugPrint(
            'DocumentSync[${_kind.api}].pull: LWW payload conflict id=${sd.id} '
            'field=payload winner=server',
          );
          await txn.logConflict(
            kind: _kind.api,
            id: sd.id,
            field: 'payload',
            local: 'local edit overwritten by server',
            server: sd.updatedAt,
          );
        }
        await txn.upsertFromServer(
          kind: _kind.api,
          id: sd.id,
          name: sd.name,
          payloadJson: sd.payloadJson,
          serverUpdatedAt: sd.updatedAt,
          syncedAt: syncedAt,
        );
        return _MergeOutcome(written: true, conflict: logged);
      }

      // Local strictly newer — keep it; it re-pushes next sync. Nothing logged.
      debugPrint(
        'DocumentSync[${_kind.api}].pull: kept newer local id=${sd.id} — '
        'server change deferred, will re-push',
      );
      return const _MergeOutcome(written: false, conflict: false);
    });
  }

  /// Apply a server tombstone (delete-wins, never silent). Logs a conflict when
  /// it clobbers an unsynced local edit, and drops the now-moot queued ops.
  Future<bool> _applyServerTombstone(String id, {required String syncedAt}) {
    return _dao.runInTransaction<bool>((txn) async {
      final localRow = await txn.getRow(_kind.api, id);
      var loggedConflict = false;

      if (localRow != null) {
        final dirty = ((localRow['dirty'] as int?) ?? 0) == 1;
        final alreadyDeleted = ((localRow['deleted'] as int?) ?? 0) == 1;
        if (dirty && !alreadyDeleted) {
          debugPrint(
            'DocumentSync[${_kind.api}].pull: tombstone conflict id=$id '
            'field=server_deleted winner=server(delete) — local edit lost',
          );
          await txn.logConflict(
            kind: _kind.api,
            id: id,
            field: 'server_deleted',
            local: 'unsynced local edit lost to server delete',
            server: null,
          );
          loggedConflict = true;
        }
        if (dirty) {
          await txn.deleteOutboxForEntity(_kind.api, id);
        }
      }

      await txn.applyServerDelete(_kind.api, id, syncedAt: syncedAt);
      return loggedConflict;
    });
  }

  // ── update coalescing ─────────────────────────────────────────────────────

  _Coalesced _coalesceUpdates(List<DocOutboxItem> queue) {
    final head = <String, int>{};
    final merged = <int, Map<String, dynamic>>{};
    final skip = <int>{};

    for (final item in queue) {
      if (item.op != DocOutboxOp.update) continue;
      final id = item.entityId;
      final headSeq = head[id];
      if (headSeq == null) {
        head[id] = item.seq;
        merged[item.seq] = Map<String, dynamic>.from(item.payload);
      } else {
        final into = merged[headSeq]!;
        for (final entry in item.payload.entries) {
          if (entry.key == 'id') continue;
          into[entry.key] = entry.value;
        }
        skip.add(item.seq);
      }
    }
    return _Coalesced(payloads: merged, skipSeqs: skip);
  }

  // ── helpers ──────────────────────────────────────────────────────────────

  DocKind _kindFromApi(String api) {
    switch (api) {
      case 'sheets':
        return DocKind.sheets;
      case 'docs':
        return DocKind.docs;
      case 'pdf':
        return DocKind.pdf;
      default:
        return _kind;
    }
  }

  static Map<String, dynamic> _decodePayload(String raw) {
    try {
      final decoded = jsonDecode(raw);
      return decoded is Map ? Map<String, dynamic>.from(decoded) : <String, dynamic>{};
    } catch (_) {
      return <String, dynamic>{};
    }
  }

  static bool _gte(String? a, String? b) {
    final av = a ?? '';
    final bv = b ?? '';
    if (av.isEmpty) return false;
    if (bv.isEmpty) return true;
    return av.compareTo(bv) >= 0;
  }

  static ApiError? _asApiError(Object e) => e is ApiError
      ? e
      : (e is DioException && e.error is ApiError ? e.error as ApiError : null);

  static int? _statusOf(Object e) {
    final api = _asApiError(e);
    if (api != null) return api.status;
    if (e is DioException) return e.response?.statusCode;
    return null;
  }

  static bool _isNetworkError(Object e) {
    final api = _asApiError(e);
    if (api != null) return api.status == 0;
    if (e is DioException) {
      if (e.type != DioExceptionType.badResponse) return true;
      return e.response?.statusCode == null;
    }
    return false;
  }
}

class _MergeOutcome {
  final bool written;
  final bool conflict;
  const _MergeOutcome({required this.written, required this.conflict});
}

/// Result of pushing one outbox item.
class _PushOutcome {
  /// True when the server accepted the op → caller commits the retire.
  final bool committed;

  /// Server-returned `updated_at` to re-base the cache row on (null = unknown).
  final String? serverUpdatedAt;

  /// True when a conflict row was logged for this push.
  final bool conflictLogged;

  const _PushOutcome({
    required this.committed,
    this.serverUpdatedAt,
    this.conflictLogged = false,
  });
}

class _Coalesced {
  final Map<int, Map<String, dynamic>> payloads;
  final Set<int> skipSeqs;
  const _Coalesced({required this.payloads, required this.skipSeqs});
}
