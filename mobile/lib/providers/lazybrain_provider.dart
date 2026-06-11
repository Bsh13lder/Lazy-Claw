import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/note.dart';
import '../repositories/lazybrain_repository.dart';
import 'auth_provider.dart';

// ── Repository provider ─────────────────────────────────────────────────────

/// Remote seam backed by Dio — injected into the notifier.
final lazyBrainRepositoryProvider = Provider<LazyBrainRepository>((ref) {
  return LazyBrainRepository(
    DioLazyBrainTransport(ref.watch(apiClientProvider)),
  );
});

// ── State ───────────────────────────────────────────────────────────────────

/// Immutable Brain screen state: journal timeline, tag counts, pinned notes,
/// and the optional tag-filter drill-down. Online-only — LazyBrain is
/// authoritative on the server (encrypted at rest), so no local cache here.
class BrainState {
  final List<Note> journal;
  final List<BrainTag> tags;
  final List<Note> pinned;
  final bool isLoading;
  final String? error;

  /// Tag drill-down: when [activeTag] is set, [tagNotes] holds its notes.
  final String? activeTag;
  final List<Note> tagNotes;
  final bool isTagLoading;
  final String? tagError;

  const BrainState({
    this.journal = const [],
    this.tags = const [],
    this.pinned = const [],
    this.isLoading = false,
    this.error,
    this.activeTag,
    this.tagNotes = const [],
    this.isTagLoading = false,
    this.tagError,
  });

  bool get isEmpty => journal.isEmpty && tags.isEmpty && pinned.isEmpty;

  BrainState copyWith({
    List<Note>? journal,
    List<BrainTag>? tags,
    List<Note>? pinned,
    bool? isLoading,
    String? error,
    bool clearError = false,
    Object? activeTag = _sentinel,
    List<Note>? tagNotes,
    bool? isTagLoading,
    String? tagError,
    bool clearTagError = false,
  }) =>
      BrainState(
        journal: journal ?? this.journal,
        tags: tags ?? this.tags,
        pinned: pinned ?? this.pinned,
        isLoading: isLoading ?? this.isLoading,
        error: clearError ? null : (error ?? this.error),
        activeTag:
            activeTag == _sentinel ? this.activeTag : activeTag as String?,
        tagNotes: tagNotes ?? this.tagNotes,
        isTagLoading: isTagLoading ?? this.isTagLoading,
        tagError: clearTagError ? null : (tagError ?? this.tagError),
      );
}

const Object _sentinel = Object();

// ── Notifier ────────────────────────────────────────────────────────────────

/// Manages the Brain power-surface state. Search and ask are one-shot
/// futures ([search]/[ask]) whose results live in screen state — they don't
/// belong in the long-lived section state.
class BrainNotifier extends StateNotifier<BrainState> {
  final LazyBrainRepository _repo;

  BrainNotifier(this._repo) : super(const BrainState());

  /// Fetch (or re-fetch) journal + tags + pinned concurrently.
  Future<void> load() async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final results = await Future.wait([
        _repo.fetchJournal(),
        _repo.fetchTags(),
        _repo.fetchPinned(),
      ]);
      state = state.copyWith(
        journal: results[0] as List<Note>,
        tags: results[1] as List<BrainTag>,
        pinned: results[2] as List<Note>,
        isLoading: false,
      );
    } catch (e) {
      state = state.copyWith(isLoading: false, error: e.toString());
    }
  }

  /// Load notes carrying [tag] into the drill-down section.
  Future<void> selectTag(String tag) async {
    state = state.copyWith(
      activeTag: tag,
      tagNotes: const <Note>[],
      isTagLoading: true,
      clearTagError: true,
    );
    try {
      final notes = await _repo.fetchNotesByTag(tag);
      // Stale-response guard: a slower fetch must not clobber a newer tag
      // selection (or a cleared one).
      if (state.activeTag != tag) return;
      state = state.copyWith(tagNotes: notes, isTagLoading: false);
    } catch (e) {
      if (state.activeTag != tag) return;
      state = state.copyWith(isTagLoading: false, tagError: e.toString());
    }
  }

  /// Clear the tag drill-down back to the section overview.
  void clearTag() {
    state = state.copyWith(
      activeTag: null,
      tagNotes: const <Note>[],
      isTagLoading: false,
      clearTagError: true,
    );
  }

  /// One-shot semantic search — result is owned by the caller (screen state).
  Future<SemanticSearchResult> search(String query, {int k = 10}) =>
      _repo.semanticSearch(query, k: k);

  /// One-shot RAG ask — result is owned by the caller (sheet state).
  Future<AskResult> ask(String question, {int k = 8}) =>
      _repo.ask(question, k: k);
}

// ── Provider ────────────────────────────────────────────────────────────────

final brainProvider = StateNotifierProvider<BrainNotifier, BrainState>((ref) {
  return BrainNotifier(ref.watch(lazyBrainRepositoryProvider));
});
