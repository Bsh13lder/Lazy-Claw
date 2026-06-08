import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../repositories/watchers_repository.dart';
import 'auth_provider.dart';

// ── Infrastructure provider ─────────────────────────────────────────────────

/// Dio-backed watchers repository, wired to the shared [ApiClient].
final watchersRepositoryProvider = Provider<WatchersRepository>((ref) {
  return WatchersRepository(DioWatchersTransport(ref.watch(apiClientProvider)));
});

// ── State ──────────────────────────────────────────────────────────────────

class WatchersState {
  final List<Watcher> watchers;
  final bool isLoading;
  final String? error;

  const WatchersState({
    this.watchers = const [],
    this.isLoading = false,
    this.error,
  });

  WatchersState copyWith({
    List<Watcher>? watchers,
    bool? isLoading,
    String? error,
  }) =>
      WatchersState(
        watchers: watchers ?? this.watchers,
        isLoading: isLoading ?? this.isLoading,
        // Passing null explicitly clears the error; omitting keeps the old one.
        error: error,
      );
}

// ── Notifier ───────────────────────────────────────────────────────────────

/// Manages the Watchers list: load, refresh, pause/resume, delete.
///
/// This is a network-only (no offline cache) notifier — Watchers is a read/
/// monitor surface; it doesn't need offline-first capabilities.
class WatchersNotifier extends StateNotifier<WatchersState> {
  final WatchersRepository _repo;

  WatchersNotifier(this._repo) : super(const WatchersState());

  /// Fetch all watchers from the server.
  Future<void> load() async {
    state = state.copyWith(isLoading: true, error: null);
    try {
      final watchers = await _repo.listWatchers();
      state = WatchersState(watchers: watchers);
    } catch (e) {
      state = WatchersState(error: e.toString());
    }
  }

  /// Pull-to-refresh: reload from the server.
  Future<void> refresh() => load();

  /// Pause a watcher then refresh the list.
  Future<void> pauseWatcher(String id) async {
    try {
      await _repo.pauseWatcher(id);
      await load();
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  /// Resume a paused watcher then refresh the list.
  Future<void> resumeWatcher(String id) async {
    try {
      await _repo.resumeWatcher(id);
      await load();
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  /// Delete a watcher then refresh the list.
  Future<void> deleteWatcher(String id) async {
    try {
      await _repo.deleteWatcher(id);
      await load();
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  void clearError() => state = state.copyWith(error: null);
}

// ── Provider ───────────────────────────────────────────────────────────────

final watchersProvider =
    StateNotifierProvider<WatchersNotifier, WatchersState>((ref) {
  return WatchersNotifier(ref.watch(watchersRepositoryProvider));
});
