import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../repositories/memory_repository.dart';
import 'auth_provider.dart';

// ── Repository provider ────────────────────────────────────────────────────

/// Remote seam backed by Dio — injected into the notifier.
final memoryRepositoryProvider = Provider<MemoryRepository>((ref) {
  return MemoryRepository(DioMemoryTransport(ref.watch(apiClientProvider)));
});

// ── State ──────────────────────────────────────────────────────────────────

class MemoryState {
  final List<PersonalMemory> memories;
  final bool isLoading;
  final bool isDeleting;
  final String? error;

  const MemoryState({
    this.memories = const [],
    this.isLoading = false,
    this.isDeleting = false,
    this.error,
  });

  MemoryState copyWith({
    List<PersonalMemory>? memories,
    bool? isLoading,
    bool? isDeleting,
    String? error,
    bool clearError = false,
  }) =>
      MemoryState(
        memories: memories ?? this.memories,
        isLoading: isLoading ?? this.isLoading,
        isDeleting: isDeleting ?? this.isDeleting,
        error: clearError ? null : (error ?? this.error),
      );
}

// ── Notifier ───────────────────────────────────────────────────────────────

/// Manages personal memory list state. Network-only (no local cache) — memory
/// is authoritative on the server and agent-written, so offline state is
/// intentionally not supported here.
class MemoryNotifier extends StateNotifier<MemoryState> {
  final MemoryRepository _repo;

  MemoryNotifier(this._repo) : super(const MemoryState());

  /// Fetch (or re-fetch) the full memory list from the server.
  Future<void> load() async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final memories = await _repo.listMemories();
      state = state.copyWith(memories: memories, isLoading: false);
    } catch (e) {
      state = state.copyWith(isLoading: false, error: e.toString());
    }
  }

  /// Delete [id] then remove it from the local list optimistically.
  ///
  /// Returns `true` on success, `false` on error (error is also stored in
  /// state so the UI can surface it).
  Future<bool> deleteMemory(String id) async {
    state = state.copyWith(isDeleting: true, clearError: true);
    try {
      await _repo.deleteMemory(id);
      final updated = state.memories.where((m) => m.id != id).toList();
      state = state.copyWith(memories: updated, isDeleting: false);
      return true;
    } catch (e) {
      state = state.copyWith(isDeleting: false, error: e.toString());
      return false;
    }
  }

  void clearError() => state = state.copyWith(clearError: true);
}

// ── Provider ───────────────────────────────────────────────────────────────

final memoryProvider =
    StateNotifierProvider<MemoryNotifier, MemoryState>((ref) {
  return MemoryNotifier(ref.watch(memoryRepositoryProvider));
});
