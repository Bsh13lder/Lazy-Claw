import 'dart:io';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../local/document_cache_dao.dart';
import '../repositories/documents_repository.dart';
import 'auth_provider.dart';
import 'tasks_provider.dart' show appDatabaseProvider;

// ── Infrastructure ─────────────────────────────────────────────────────────

/// Dio-backed [DocumentsRepository] wired to the shared [ApiClient].
final documentsRepositoryProvider = Provider<DocumentsRepository>((ref) {
  return DocumentsRepository(DioDocumentsTransport(ref.watch(apiClientProvider)));
});

/// On-device read-through cache for the office suite. Null when the local DB
/// isn't available (e.g. provider not overridden in a widget test) so the
/// Documents tab degrades gracefully to network-only.
final documentCacheDaoProvider = Provider<DocumentCacheDao?>((ref) {
  try {
    return DocumentCacheDao(ref.watch(appDatabaseProvider));
  } catch (_) {
    return null;
  }
});

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

  DocumentsListNotifier(this._repo, this._kind, {DocumentCacheDao? cache})
      : _cache = cache,
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

  /// Pull-to-refresh: re-fetch without the full-screen loader so the existing
  /// list stays visible.
  Future<void> refresh() async {
    state = state.copyWith(clearError: true);
    await _revalidate(coldMiss: false);
  }

  /// Network revalidation shared by [load] and [refresh]. On failure, keep any
  /// list already on screen and only surface an error when there's nothing to
  /// show (a true cold miss).
  Future<void> _revalidate({required bool coldMiss}) async {
    try {
      final items = await _repo.list(_kind);
      state = state.copyWith(items: items, isLoading: false);
      await _writeCachedList(items);
    } catch (e) {
      if (coldMiss && state.items.isEmpty) {
        state = state.copyWith(isLoading: false, error: _friendlyError(e));
      } else {
        state = state.copyWith(isLoading: false);
      }
    }
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

  /// Create a blank sheet/doc and prepend it, returning the new meta (or null
  /// on failure — the error is surfaced in state).
  Future<DocMeta?> createBlank(String name) async {
    if (_kind == DocKind.pdf) return null;
    state = state.copyWith(clearError: true);
    try {
      final meta = await _repo.create(_kind, name);
      state = state.copyWith(items: [meta, ...state.items]);
      return meta;
    } catch (e) {
      state = state.copyWith(error: _friendlyError(e));
      return null;
    }
  }

  /// Import a PDF and prepend it, returning the new meta (or null on failure).
  Future<DocMeta?> importPdf(File file) async {
    if (_kind != DocKind.pdf) return null;
    state = state.copyWith(clearError: true);
    try {
      final meta = await _repo.importPdf(file);
      state = state.copyWith(items: [meta, ...state.items]);
      return meta;
    } catch (e) {
      state = state.copyWith(error: _friendlyError(e));
      return null;
    }
  }

  /// Import a file as a new document, routing to the right endpoint by kind
  /// (xlsx → sheets, docx → docs, pdf → pdf). Prepends the new meta; null on
  /// failure (the error is surfaced in state).
  Future<DocMeta?> import(File file) async {
    state = state.copyWith(clearError: true);
    try {
      final meta = switch (_kind) {
        DocKind.sheets => await _repo.importSheet(file),
        DocKind.docs => await _repo.importDoc(file),
        DocKind.pdf => await _repo.importPdf(file),
      };
      state = state.copyWith(items: [meta, ...state.items]);
      return meta;
    } catch (e) {
      state = state.copyWith(error: _friendlyError(e));
      return null;
    }
  }

  /// Delete [id] then drop it from the list (and from the on-device cache).
  Future<void> delete(String id) async {
    state = state.copyWith(deletingId: id, clearError: true);
    try {
      await _repo.delete(_kind, id);
      final next = state.items.where((d) => d.id != id).toList();
      state = state.copyWith(items: next, clearDeleting: true);
      try {
        await _cache?.deleteDoc(_kind.api, id);
        await _cache?.putList(_kind.api, next.map((m) => m.toJson()).toList());
      } catch (_) {
        // Cache eviction is best-effort.
      }
    } catch (e) {
      state = state.copyWith(clearDeleting: true, error: _friendlyError(e));
    }
  }

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
/// independent and alive while the Documents screen is mounted.
final documentsListProvider = StateNotifierProvider.family<
    DocumentsListNotifier, DocumentsListState, DocKind>((ref, kind) {
  return DocumentsListNotifier(
    ref.watch(documentsRepositoryProvider),
    kind,
    cache: ref.watch(documentCacheDaoProvider),
  );
});
