import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../repositories/activity_repository.dart';
import 'auth_provider.dart';

// ── Infrastructure providers ───────────────────────────────────────────────

final activityRepositoryProvider = Provider<ActivityRepository>((ref) {
  return ActivityRepository(DioActivityTransport(ref.watch(apiClientProvider)));
});

// ── State ──────────────────────────────────────────────────────────────────

class ActivityState {
  final AgentActivitySnapshot snapshot;
  final bool isLoading;
  final String? error;

  const ActivityState({
    this.snapshot = const AgentActivitySnapshot(),
    this.isLoading = false,
    this.error,
  });

  ActivityState copyWith({
    AgentActivitySnapshot? snapshot,
    bool? isLoading,
    String? error,
    bool clearError = false,
  }) =>
      ActivityState(
        snapshot: snapshot ?? this.snapshot,
        isLoading: isLoading ?? this.isLoading,
        error: clearError ? null : (error ?? this.error),
      );

  bool get hasData => !snapshot.isEmpty;
}

// ── Notifier ───────────────────────────────────────────────────────────────

class ActivityNotifier extends StateNotifier<ActivityState> {
  final ActivityRepository _repo;

  ActivityNotifier(this._repo) : super(const ActivityState());

  /// Initial load — shows the skeleton while the first snapshot arrives.
  Future<void> load() async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final snap = await _repo.getStatus();
      state = state.copyWith(snapshot: snap, isLoading: false, clearError: true);
    } catch (e) {
      state = state.copyWith(isLoading: false, error: e.toString());
    }
  }

  /// Silent reload used by pull-to-refresh and the poll timer.
  ///
  /// Never flips [ActivityState.isLoading] (no skeleton flicker every few
  /// seconds), keeps the previous snapshot on a transient failure, and only
  /// surfaces an error when there's nothing on screen to fall back to.
  Future<void> refresh() async {
    try {
      final snap = await _repo.getStatus();
      state = state.copyWith(snapshot: snap, clearError: true);
    } catch (e) {
      if (!state.hasData) {
        state = state.copyWith(error: e.toString());
      }
    }
  }

  /// Cancel one running task, then refresh so the row flips to
  /// cancelling/settled. Returns false when the cancel didn't fire (task
  /// already settled, network error) so the UI can tell the user.
  Future<bool> cancelTask(String taskId) async {
    bool ok;
    try {
      ok = await _repo.cancelTask(taskId);
    } catch (_) {
      ok = false;
    }
    await refresh();
    return ok;
  }

  /// Cancel everything running, then refresh. Returns the number of tasks
  /// the server fired cancellations for (0 on failure).
  Future<int> cancelAll() async {
    int count;
    try {
      count = await _repo.cancelAll();
    } catch (_) {
      count = 0;
    }
    await refresh();
    return count;
  }
}

// ── Provider ───────────────────────────────────────────────────────────────

final activityProvider =
    StateNotifierProvider<ActivityNotifier, ActivityState>((ref) {
  return ActivityNotifier(ref.watch(activityRepositoryProvider));
});
