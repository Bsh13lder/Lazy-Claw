import 'dart:io';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../repositories/documents_repository.dart';
import 'auth_provider.dart';

// ── Infrastructure ─────────────────────────────────────────────────────────

/// Dio-backed [DocumentsRepository] wired to the shared [ApiClient].
final documentsRepositoryProvider = Provider<DocumentsRepository>((ref) {
  return DocumentsRepository(DioDocumentsTransport(ref.watch(apiClientProvider)));
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

/// Drives one kind's document list — network-first (no offline cache; the
/// office suite is decrypted server-side per request).
class DocumentsListNotifier extends StateNotifier<DocumentsListState> {
  final DocumentsRepository _repo;
  final DocKind _kind;

  DocumentsListNotifier(this._repo, this._kind)
      : super(const DocumentsListState());

  /// Load (or reload) the list with the full-screen loading flag.
  Future<void> load() async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final items = await _repo.list(_kind);
      state = state.copyWith(items: items, isLoading: false);
    } catch (e) {
      state = state.copyWith(isLoading: false, error: _friendlyError(e));
    }
  }

  /// Pull-to-refresh: re-fetch without the full-screen loader so the existing
  /// list stays visible.
  Future<void> refresh() async {
    state = state.copyWith(clearError: true);
    try {
      final items = await _repo.list(_kind);
      state = state.copyWith(items: items);
    } catch (e) {
      state = state.copyWith(error: _friendlyError(e));
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

  /// Delete [id] then drop it from the list.
  Future<void> delete(String id) async {
    state = state.copyWith(deletingId: id, clearError: true);
    try {
      await _repo.delete(_kind, id);
      state = state.copyWith(
        items: state.items.where((d) => d.id != id).toList(),
        clearDeleting: true,
      );
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
  return DocumentsListNotifier(ref.watch(documentsRepositoryProvider), kind);
});
