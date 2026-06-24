import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../local/document_cache_dao.dart';
import '../repositories/documents_repository.dart';
import '../screens/documents/blank_doc.dart';
import '../sync/document_sync.dart';
import 'auth_provider.dart';
import 'tasks_provider.dart' show appDatabaseProvider, reachableProvider;

// ── Infrastructure ─────────────────────────────────────────────────────────

/// Dio-backed [DocumentsRepository] wired to the shared [ApiClient].
final documentsRepositoryProvider = Provider<DocumentsRepository>((ref) {
  return DocumentsRepository(DioDocumentsTransport(ref.watch(apiClientProvider)));
});

/// On-device offline-first cache + sync source for the office suite. Null when
/// the local DB isn't available (e.g. provider not overridden in a widget test)
/// so the Documents tab degrades gracefully to network-only.
final documentCacheDaoProvider = Provider<DocumentCacheDao?>((ref) {
  try {
    return DocumentCacheDao(ref.watch(appDatabaseProvider));
  } catch (_) {
    return null;
  }
});

/// One [DocumentSync] engine per [DocKind]. Null when the local DB is
/// unavailable (the Documents tab then stays network-only). Used by the
/// foreground/background schedulers and the reachability-flip hook.
final documentSyncProvider =
    Provider.family<DocumentSync?, DocKind>((ref, kind) {
  final dao = ref.watch(documentCacheDaoProvider);
  if (dao == null) return null;
  return DocumentSync(dao, ref.watch(documentsRepositoryProvider), kind);
});

/// Run one sync pass for all three document kinds (best-effort). [read] is any
/// provider reader (`WidgetRef.read` or `Ref.read`), so the foreground scheduler
/// in main.dart can drive it alongside tasks/notes/budgets without a type clash.
Future<void> syncAllDocuments(
  DocumentSync? Function(ProviderListenable<DocumentSync?>) read,
) async {
  for (final kind in DocKind.values) {
    final engine = read(documentSyncProvider(kind));
    if (engine == null) continue;
    try {
      await engine.sync();
    } catch (_) {
      // Offline / server down is a normal state — the cache holds the truth.
    }
  }
}

// ── List state ─────────────────────────────────────────────────────────────

/// Immutable state for one kind's document list.
class DocumentsListState {
  final List<DocMeta> items;
  final bool isLoading;
  final String? error;

  /// Id of an item currently being deleted (shows a spinner on that row).
  final String? deletingId;

  const DocumentsListState({
    this.items = const [],
    this.isLoading = false,
    this.error,
    this.deletingId,
  });

  DocumentsListState copyWith({
    List<DocMeta>? items,
    bool? isLoading,
    String? error,
    bool clearError = false,
    String? deletingId,
    bool clearDeleting = false,
  }) =>
      DocumentsListState(
        items: items ?? this.items,
        isLoading: isLoading ?? this.isLoading,
        error: clearError ? null : (error ?? this.error),
        deletingId: clearDeleting ? null : (deletingId ?? this.deletingId),
      );
}

// ── List notifier ──────────────────────────────────────────────────────────

/// Drives one kind's document list — cache-first (stale-while-revalidate): paint
/// the on-device cached index instantly, then refresh over the network. The
/// documents themselves are still server-owned and edited online.
class DocumentsListNotifier extends StateNotifier<DocumentsListState> {
  final DocumentsRepository _repo;
  final DocKind _kind;
  final DocumentCacheDao? _cache;
  final DocumentSync? _sync;

  DocumentsListNotifier(
    this._repo,
    this._kind, {
    DocumentCacheDao? cache,
    DocumentSync? sync,
  })  : _cache = cache,
        _sync = sync,
        super(const DocumentsListState());

  /// Load: paint the cached index immediately (no spinner), then revalidate.
  Future<void> load() async {
    final cached = await _readCachedList();
    if (cached != null && cached.isNotEmpty) {
      state = state.copyWith(items: cached, isLoading: false, clearError: true);
    } else {
      state = state.copyWith(isLoading: true, clearError: true);
    }
    await _revalidate(coldMiss: cached == null || cached.isEmpty);
  }

  /// Pull-to-refresh: force a sync (drain outbox + pull /changes) then re-fetch
  /// without the full-screen loader so the existing list stays visible.
  Future<void> refresh() async {
    state = state.copyWith(clearError: true);
    await _syncOnce();
    await _revalidate(coldMiss: false);
  }

  /// Run one sync pass for this kind (best-effort). Surfaces server-created /
  /// edited docs into the local cache so they appear in the list — this is the
  /// fix for "web edits don't show on Flutter".
  Future<void> _syncOnce() async {
    final engine = _sync;
    if (engine == null) return;
    try {
      await engine.sync();
    } catch (_) {
      // Offline / server down is normal — the cache holds the truth.
    }
  }

  /// Network revalidation shared by [load] and [refresh]. Merges the network
  /// list with the local cache (dirty/local-only docs + server deltas already
  /// pulled into the cache) so nothing on either side is dropped. On failure,
  /// keep any list already on screen (or fall back to the cache) and only
  /// surface an error when there's truly nothing to show.
  Future<void> _revalidate({required bool coldMiss}) async {
    await _syncOnce();
    try {
      final networkItems = await _repo.list(_kind);
      final merged = await _mergeWithCache(networkItems);
      state = state.copyWith(items: merged, isLoading: false);
      await _writeCachedList(merged);
    } catch (e) {
      // Network failed — fall back to the merged cache so local-first creates
      // and previously-pulled server docs still render offline.
      final cacheOnly = await _mergeWithCache(const []);
      if (cacheOnly.isNotEmpty) {
        state = state.copyWith(items: cacheOnly, isLoading: false);
      } else if (coldMiss && state.items.isEmpty) {
        state = state.copyWith(isLoading: false, error: _friendlyError(e));
      } else {
        state = state.copyWith(isLoading: false);
      }
    }
  }

  /// Union the network list with the on-device cache: keep every non-deleted
  /// local doc (local-only creates that haven't pushed yet, plus server docs the
  /// /changes pull already landed), preferring local metadata for dirty rows.
  /// Network rows win for clean docs (freshest server name/updated_at).
  Future<List<DocMeta>> _mergeWithCache(List<DocMeta> networkItems) async {
    final cache = _cache;
    if (cache == null) return networkItems;
    List<CachedDoc> cachedDocs;
    try {
      cachedDocs = await cache.listDocs(_kind.api);
    } catch (_) {
      return networkItems;
    }
    final byId = <String, DocMeta>{};
    // Seed with cache rows so local-only docs are present.
    for (final d in cachedDocs) {
      byId[d.id] = DocMeta(id: d.id, name: d.name, updatedAt: d.updatedAt);
    }
    // Overlay network rows (clean server truth wins for non-dirty docs).
    final dirty = cachedDocs.where((d) => d.dirty).map((d) => d.id).toSet();
    for (final m in networkItems) {
      if (dirty.contains(m.id)) continue; // keep local dirty metadata
      byId[m.id] = m;
    }
    final out = byId.values.toList()
      ..sort((a, b) => (b.updatedAt ?? '').compareTo(a.updatedAt ?? ''));
    return out;
  }

  Future<List<DocMeta>?> _readCachedList() async {
    final cache = _cache;
    if (cache == null) return null;
    try {
      final raw = await cache.getList(_kind.api);
      return raw?.map(DocMeta.fromJson).toList();
    } catch (_) {
      return null;
    }
  }

  Future<void> _writeCachedList(List<DocMeta> items) async {
    try {
      await _cache?.putList(_kind.api, items.map((m) => m.toJson()).toList());
    } catch (_) {
      // Cache write is best-effort — never let it break the list.
    }
  }

  /// Create a blank sheet/doc LOCAL-FIRST: mint a client UUID, write a dirty
  /// cache row + enqueue an outbox create, prepend it instantly, then sync in
  /// the background. Returns the new meta (or null when not applicable).
  Future<DocMeta?> createBlank(String name) async {
    if (_kind == DocKind.pdf) return null;
    state = state.copyWith(clearError: true);
    final cache = _cache;
    if (cache == null) {
      // No local DB — fall back to a direct network create.
      try {
        final meta = await _repo.create(_kind, name);
        state = state.copyWith(items: [meta, ...state.items]);
        return meta;
      } catch (e) {
        state = state.copyWith(error: _friendlyError(e));
        return null;
      }
    }
    try {
      // Build a valid blank Univer payload on the client so the new doc is
      // immediately editable offline (cache-first open) AND the outbox `create`
      // carries a real workbook. Without this the cache row + outbox op stored a
      // null payload → an empty, sheet-less, uneditable grid (the regression).
      final blank = _blankPayloadJson(name);
      final id = await cache.applyLocalCreate(
        kind: _kind.api,
        name: name,
        payloadJson: blank,
      );
      final meta = DocMeta(
        id: id,
        name: name,
        updatedAt: DateTime.now().toUtc().toIso8601String(),
      );
      state = state.copyWith(items: [meta, ...state.items]);
      unawaited(_syncThenMerge());
      return meta;
    } catch (e) {
      state = state.copyWith(error: _friendlyError(e));
      return null;
    }
  }

  /// Encode the kind-appropriate blank Univer snapshot for a local-first
  /// create. Returns null for PDF (handled before this is ever reached) so a
  /// stray call can't enqueue a bogus PDF payload.
  String? _blankPayloadJson(String name) {
    switch (_kind) {
      case DocKind.sheets:
        return jsonEncode(blankWorkbook(name));
      case DocKind.docs:
        return jsonEncode(blankDocument(name));
      case DocKind.pdf:
        return null;
    }
  }

  /// Import a PDF and prepend it, returning the new meta (or null on failure).
  /// PDFs are import-only (server-side multipart), so this stays network-first;
  /// the result is cached so it survives offline + propagates on the next pull.
  Future<DocMeta?> importPdf(File file) async {
    if (_kind != DocKind.pdf) return null;
    state = state.copyWith(clearError: true);
    try {
      final meta = await _repo.importPdf(file);
      await _cacheMeta(meta);
      state = state.copyWith(items: [meta, ...state.items]);
      return meta;
    } catch (e) {
      state = state.copyWith(error: _friendlyError(e));
      return null;
    }
  }

  /// Import a file as a new document, routing to the right endpoint by kind
  /// (xlsx → sheets, docx → docs, pdf → pdf). Imports go through the server's
  /// multipart parser, so they stay network-first; the result is cached.
  Future<DocMeta?> import(File file) async {
    state = state.copyWith(clearError: true);
    try {
      final meta = switch (_kind) {
        DocKind.sheets => await _repo.importSheet(file),
        DocKind.docs => await _repo.importDoc(file),
        DocKind.pdf => await _repo.importPdf(file),
      };
      await _cacheMeta(meta);
      state = state.copyWith(items: [meta, ...state.items]);
      return meta;
    } catch (e) {
      state = state.copyWith(error: _friendlyError(e));
      return null;
    }
  }

  /// Delete [id] LOCAL-FIRST: tombstone the cache row + enqueue an outbox
  /// delete, drop it from the list instantly, then push in the background.
  Future<void> delete(String id) async {
    state = state.copyWith(deletingId: id, clearError: true);
    final cache = _cache;
    if (cache == null) {
      // No local DB — direct network delete.
      try {
        await _repo.delete(_kind, id);
        final next = state.items.where((d) => d.id != id).toList();
        state = state.copyWith(items: next, clearDeleting: true);
      } catch (e) {
        state = state.copyWith(clearDeleting: true, error: _friendlyError(e));
      }
      return;
    }
    try {
      // Ensure a row exists to tombstone (server-only docs may not be cached as
      // sync rows yet); if applyLocalDelete is a no-op, fall back to a tombstone
      // via a direct delete enqueue is unnecessary — listDocs filters deleted.
      final tomb = await cache.applyLocalDelete(_kind.api, id);
      if (!tomb) {
        // Not in the sync cache — do a best-effort direct network delete so the
        // server row goes away; the next pull won't resurrect it.
        try {
          await _repo.delete(_kind, id);
        } catch (_) {/* surfaced via offline state; pull reconciles */}
      }
      final next = state.items.where((d) => d.id != id).toList();
      state = state.copyWith(items: next, clearDeleting: true);
      try {
        await _cache?.putList(_kind.api, next.map((m) => m.toJson()).toList());
      } catch (_) {}
      unawaited(_syncThenMerge());
    } catch (e) {
      state = state.copyWith(clearDeleting: true, error: _friendlyError(e));
    }
  }

  /// Best-effort: cache a freshly imported doc's metadata as a CLEAN row so it
  /// survives offline + isn't wiped by a later pull before the server lists it.
  Future<void> _cacheMeta(DocMeta meta) async {
    try {
      await _cache?.putServerDoc(
        kind: _kind.api,
        id: meta.id,
        name: meta.name,
        updatedAt: meta.updatedAt,
      );
    } catch (_) {}
  }

  /// Background: sync this kind then re-merge the list so the result of a
  /// local-first mutation (server-stamped id/updated_at) lands on screen.
  Future<void> _syncThenMerge() async {
    await _syncOnce();
    if (!mounted) return;
    try {
      final merged = await _mergeWithCache(const []);
      state = state.copyWith(items: merged);
      await _writeCachedList(merged);
    } catch (_) {/* best-effort */}
  }

  /// Public hook for the reachability flip / external schedulers: sync + merge.
  Future<void> syncNow() => _syncThenMerge();

  void clearError() => state = state.copyWith(clearError: true);

  static String _friendlyError(Object e) {
    final msg = e.toString();
    if (msg.contains('SocketException') || msg.contains('Connection refused')) {
      return 'Cannot reach the server. Check your connection.';
    }
    if (msg.contains('401') || msg.contains('Unauthorized')) {
      return 'Session expired. Please log in again.';
    }
    return 'Something went wrong. Pull to retry.';
  }
}

/// One list notifier per [DocKind] (family). Keeps each sub-tab's state
/// independent and alive while the Documents screen is mounted. When the backend
/// comes back online, drains the outbox + pulls deltas so local-first edits push
/// and server-side edits surface.
final documentsListProvider = StateNotifierProvider.family<
    DocumentsListNotifier, DocumentsListState, DocKind>((ref, kind) {
  final notifier = DocumentsListNotifier(
    ref.watch(documentsRepositoryProvider),
    kind,
    cache: ref.watch(documentCacheDaoProvider),
    sync: ref.watch(documentSyncProvider(kind)),
  );

  ref.listen<bool>(reachableProvider, (prev, next) {
    if (next == true && prev != true) {
      notifier.syncNow();
    }
  });

  return notifier;
});
