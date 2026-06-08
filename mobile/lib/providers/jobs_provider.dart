import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../repositories/jobs_repository.dart';
import 'auth_provider.dart';

// ── Infrastructure ─────────────────────────────────────────────────────────

/// Dio-backed [JobsRepository] wired to the shared [ApiClient].
final jobsRepositoryProvider = Provider<JobsRepository>((ref) {
  return JobsRepository(DioJobsTransport(ref.watch(apiClientProvider)));
});

// ── State ──────────────────────────────────────────────────────────────────

class JobsState {
  final List<Job> jobs;
  final bool isLoading;
  final String? error;

  /// Id of a job currently being toggled (pause/resume) — shows a spinner on
  /// that card while the request is in flight.
  final String? togglingId;

  const JobsState({
    this.jobs = const [],
    this.isLoading = false,
    this.error,
    this.togglingId,
  });

  JobsState copyWith({
    List<Job>? jobs,
    bool? isLoading,
    String? error,
    bool clearError = false,
    String? togglingId,
    bool clearToggling = false,
  }) =>
      JobsState(
        jobs: jobs ?? this.jobs,
        isLoading: isLoading ?? this.isLoading,
        error: clearError ? null : (error ?? this.error),
        togglingId: clearToggling ? null : (togglingId ?? this.togglingId),
      );

  // ── Computed views ────────────────────────────────────────────────────────

  List<Job> get recurringJobs => jobs.where((j) => j.isRecurring).toList();
  List<Job> get oneOffJobs => jobs.where((j) => !j.isRecurring).toList();
}

// ── Notifier ───────────────────────────────────────────────────────────────

/// Drives the Jobs screen — network-first (no offline cache needed for
/// server-owned scheduled jobs).
class JobsNotifier extends StateNotifier<JobsState> {
  final JobsRepository _repo;

  JobsNotifier(this._repo) : super(const JobsState());

  /// Load (or reload) the job list from the server.
  Future<void> load() async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final jobs = await _repo.listJobs();
      state = state.copyWith(jobs: jobs, isLoading: false);
    } catch (e) {
      state = state.copyWith(
        isLoading: false,
        error: _friendlyError(e),
      );
    }
  }

  /// Pull-to-refresh: re-fetch without setting the full-screen loading flag so
  /// the existing list stays visible while the request is in flight.
  Future<void> refresh() async {
    state = state.copyWith(clearError: true);
    try {
      final jobs = await _repo.listJobs();
      state = state.copyWith(jobs: jobs);
    } catch (e) {
      state = state.copyWith(error: _friendlyError(e));
    }
  }

  /// Pause [id] (active → paused) then reload.
  Future<void> pauseJob(String id) async {
    state = state.copyWith(togglingId: id, clearError: true);
    try {
      await _repo.pauseJob(id);
      await _reloadJobs();
    } catch (e) {
      state = state.copyWith(clearToggling: true, error: _friendlyError(e));
    }
  }

  /// Resume [id] (paused → active) then reload.
  Future<void> resumeJob(String id) async {
    state = state.copyWith(togglingId: id, clearError: true);
    try {
      await _repo.resumeJob(id);
      await _reloadJobs();
    } catch (e) {
      state = state.copyWith(clearToggling: true, error: _friendlyError(e));
    }
  }

  void clearError() => state = state.copyWith(clearError: true);

  // ── internals ──────────────────────────────────────────────────────────

  Future<void> _reloadJobs() async {
    try {
      final jobs = await _repo.listJobs();
      state = state.copyWith(jobs: jobs, clearToggling: true);
    } catch (e) {
      state = state.copyWith(clearToggling: true, error: _friendlyError(e));
    }
  }

  static String _friendlyError(Object e) {
    final msg = e.toString();
    if (msg.contains('SocketException') || msg.contains('Connection refused')) {
      return 'Cannot reach the server. Check your connection.';
    }
    if (msg.contains('401') || msg.contains('Unauthorized')) {
      return 'Session expired. Please log in again.';
    }
    return 'Failed to load jobs. Pull to retry.';
  }
}

// ── Provider ───────────────────────────────────────────────────────────────

final jobsProvider = StateNotifierProvider<JobsNotifier, JobsState>((ref) {
  return JobsNotifier(ref.watch(jobsRepositoryProvider));
});
