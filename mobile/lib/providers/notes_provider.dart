import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api/api_exceptions.dart';
import '../models/note.dart';
import '../repositories/notes_repository.dart';
import 'auth_provider.dart';

// ── Repository provider ────────────────────────────────────────────────────

final notesRepositoryProvider = Provider<NotesRepository>((ref) {
  return NotesRepository(DioNotesTransport(ref.watch(apiClientProvider)));
});

// ── State ──────────────────────────────────────────────────────────────────

class NotesState {
  final List<Note> notes;
  final bool isLoading;
  final String? error;
  final String searchQuery;

  const NotesState({
    this.notes = const [],
    this.isLoading = false,
    this.error,
    this.searchQuery = '',
  });

  NotesState copyWith({
    List<Note>? notes,
    bool? isLoading,
    String? error,
    String? searchQuery,
  }) =>
      NotesState(
        notes: notes ?? this.notes,
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

class NotesNotifier extends StateNotifier<NotesState> {
  final NotesRepository _repo;
  NotesNotifier(this._repo) : super(const NotesState());

  Future<void> load() async {
    state = state.copyWith(isLoading: true, error: null);
    try {
      final notes = await _repo.listNotes();
      state = NotesState(notes: notes, isLoading: false);
    } on ApiError catch (e) {
      state = NotesState(error: e.message, isLoading: false);
    } catch (e) {
      state = NotesState(error: e.toString(), isLoading: false);
    }
  }

  Future<void> search(String query) async {
    final q = query.trim();
    if (q.isEmpty) {
      // Clear search — go back to the full list.
      state = state.copyWith(searchQuery: '');
      await load();
      return;
    }
    state = state.copyWith(isLoading: true, error: null, searchQuery: q);
    try {
      final notes = await _repo.search(q);
      state = state.copyWith(notes: notes, isLoading: false);
    } on ApiError catch (e) {
      state = state.copyWith(error: e.message, isLoading: false);
    } catch (e) {
      state = state.copyWith(error: e.toString(), isLoading: false);
    }
  }

  Future<Note?> createNote({String? title, required String content}) async {
    try {
      final note = await _repo.createNote(title: title, content: content);
      // Immutable prepend — do not mutate existing list.
      state = state.copyWith(notes: [note, ...state.notes]);
      return note;
    } on ApiError catch (e) {
      state = state.copyWith(error: e.message);
      return null;
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
      final updated =
          await _repo.updateNote(id, title: title, content: content);
      // Immutable replace — return new list with updated note.
      final newList = state.notes.map((n) => n.id == id ? updated : n).toList();
      state = state.copyWith(notes: newList);
    } on ApiError catch (e) {
      state = state.copyWith(error: e.message);
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  Future<void> deleteNote(String id) async {
    // Optimistic remove.
    final without = state.notes.where((n) => n.id != id).toList();
    state = state.copyWith(notes: without);

    try {
      await _repo.deleteNote(id);
    } on ApiError catch (e) {
      state = state.copyWith(error: e.message);
      await load();
    } catch (e) {
      state = state.copyWith(error: e.toString());
      await load();
    }
  }

  void clearError() => state = state.copyWith(error: null);
}

// ── Providers ──────────────────────────────────────────────────────────────

final notesProvider =
    StateNotifierProvider<NotesNotifier, NotesState>((ref) {
  return NotesNotifier(ref.watch(notesRepositoryProvider));
});
