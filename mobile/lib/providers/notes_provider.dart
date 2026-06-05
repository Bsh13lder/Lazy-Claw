import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../local/note_dao.dart';
import '../models/note.dart';
import '../repositories/notes_repository.dart';
import '../sync/note_sync.dart';
import 'auth_provider.dart';
// Reuse the shared offline-first infrastructure (DB handle + reachability)
// declared once in tasks_provider — these are generic, not task-specific.
import 'tasks_provider.dart' show appDatabaseProvider, reachableProvider;

// ── Infrastructure providers ────────────────────────────────────────────────

/// Local note store backed by the encrypted DB.
final noteDaoProvider = Provider<NoteDao>((ref) {
  return NoteDao(ref.watch(appDatabaseProvider));
});

/// Remote seam (Dio-backed). Same transport as before — now only used by the
/// sync engine, never directly by the UI.
final notesRepositoryProvider = Provider<NotesRepository>((ref) {
  return NotesRepository(DioNotesTransport(ref.watch(apiClientProvider)));
});

/// The offline-first sync engine for notes.
final noteSyncProvider = Provider<NoteSync>((ref) {
  return NoteSync(
    ref.watch(noteDaoProvider),
    ref.watch(notesRepositoryProvider),
  );
});

// ── State ──────────────────────────────────────────────────────────────────

class NotesState {
  final List<Note> notes;

  /// Ids with un-pushed local edits — the UI shows a cloud-off badge on these.
  final Set<String> dirtyIds;
  final bool isLoading;
  final String? error;
  final String searchQuery;

  const NotesState({
    this.notes = const [],
    this.dirtyIds = const {},
    this.isLoading = false,
    this.error,
    this.searchQuery = '',
  });

  NotesState copyWith({
    List<Note>? notes,
    Set<String>? dirtyIds,
    bool? isLoading,
    String? error,
    String? searchQuery,
  }) =>
      NotesState(
        notes: notes ?? this.notes,
        dirtyIds: dirtyIds ?? this.dirtyIds,
        isLoading: isLoading ?? this.isLoading,
        error: error,
        searchQuery: searchQuery ?? this.searchQuery,
      );

  /// Pinned notes first, then by updated_at descending.
  List<Note> get sortedNotes {
    final sorted = List<Note>.from(notes);
    sorted.sort((a, b) {
      if (a.pinned && !b.pinned) return -1;
      if (!a.pinned && b.pinned) return 1;
      return b.updatedAt.compareTo(a.updatedAt);
    });
    return sorted;
  }
}

// ── Notifier ───────────────────────────────────────────────────────────────

/// Offline-first notes notifier.
///
/// Reads come from the local DAO (instant, works offline). Writes go to the
/// DAO + outbox first (optimistic), then a best-effort [NoteSync.sync] pushes
/// them when the backend is reachable. The UI never blocks on the network.
///
/// SEARCH IS LOCAL: the search box filters the on-device cache (title + content
/// substring, case-insensitive) so it keeps working while the backend is
/// unreachable. No `/api/lazybrain/search` call is made.
class NotesNotifier extends StateNotifier<NotesState> {
  final NoteDao _dao;
  final NoteSync _sync;

  NotesNotifier(this._dao, this._sync) : super(const NotesState());

  /// Load from the local cache immediately, then kick a background sync and
  /// refresh from cache again when it settles.
  Future<void> load() async {
    state = state.copyWith(isLoading: true, error: null, searchQuery: '');
    await _refreshFromCache(loading: false);
    // Best-effort sync; failures are silent (offline is a normal state).
    unawaited(_syncThenRefresh());
  }

  /// Pull-to-refresh: force a sync then re-read the cache (preserving any
  /// active search filter).
  Future<void> refresh() async {
    await _syncThenRefresh();
  }

  /// Trigger a sync (e.g. when reachability flips to true) then refresh.
  Future<void> syncNow() => _syncThenRefresh();

  /// Filter the LOCAL cache by [query] (title + content substring). Empty query
  /// clears the filter and shows the full list. Never hits the network, so it
  /// works fully offline.
  Future<void> search(String query) async {
    final q = query.trim();
    if (q.isEmpty) {
      state = state.copyWith(searchQuery: '');
      await _refreshFromCache();
      return;
    }
    final all = await _dao.list();
    final needle = q.toLowerCase();
    final filtered = all.where((n) {
      final inTitle = (n.title ?? '').toLowerCase().contains(needle);
      final inContent = n.content.toLowerCase().contains(needle);
      final inTags = n.tags.any((t) => t.toLowerCase().contains(needle));
      return inTitle || inContent || inTags;
    }).toList();
    final dirty = await _dao.dirtyIds();
    state = state.copyWith(
      notes: filtered,
      dirtyIds: dirty,
      isLoading: false,
      searchQuery: q,
    );
  }

  Future<Note?> createNote({String? title, required String content}) async {
    try {
      final note = await _dao.applyLocalCreate(content: content, title: title);
      await _refreshFromCache();
      unawaited(_syncThenRefresh());
      return note;
    } catch (e) {
      state = state.copyWith(error: e.toString());
      return null;
    }
  }

  Future<void> updateNote(
    String id, {
    String? title,
    String? content,
  }) async {
    try {
      await _dao.applyLocalUpdate(id, title: title, content: content);
      await _refreshFromCache();
      unawaited(_syncThenRefresh());
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  Future<void> deleteNote(String id) async {
    try {
      await _dao.applyLocalDelete(id);
      await _refreshFromCache();
      unawaited(_syncThenRefresh());
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  void clearError() => state = state.copyWith(error: null);

  // ── internals ──────────────────────────────────────────────────────────

  /// Re-read the cache. When a search filter is active, re-apply it so a sync
  /// that lands new rows doesn't blow away the user's filtered view.
  Future<void> _refreshFromCache({bool loading = false}) async {
    if (state.searchQuery.isNotEmpty) {
      await search(state.searchQuery);
      return;
    }
    final notes = await _dao.list();
    final dirty = await _dao.dirtyIds();
    state = state.copyWith(
      notes: notes,
      dirtyIds: dirty,
      isLoading: loading,
      searchQuery: state.searchQuery,
    );
  }

  Future<void> _syncThenRefresh() async {
    try {
      await _sync.sync();
    } catch (_) {
      // Offline / server down — the local cache already holds the truth.
    }
    if (mounted) await _refreshFromCache();
  }
}

// ── Providers ──────────────────────────────────────────────────────────────

final notesProvider =
    StateNotifierProvider<NotesNotifier, NotesState>((ref) {
  final notifier = NotesNotifier(
    ref.watch(noteDaoProvider),
    ref.watch(noteSyncProvider),
  );

  // When the backend comes back online, drain the outbox + pull deltas.
  ref.listen<bool>(reachableProvider, (prev, next) {
    if (next == true && prev != true) {
      notifier.syncNow();
    }
  });

  return notifier;
});
