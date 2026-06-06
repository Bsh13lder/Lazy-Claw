import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:sqflite_sqlcipher/sqflite.dart';

import '../local/app_db.dart' show DbHealth;
import '../local/task_dao.dart';
import '../models/task.dart';
import '../repositories/tasks_repository.dart';
import '../sync/reachability.dart';
import '../sync/task_sync.dart';
import 'auth_provider.dart';

// ── Infrastructure providers ────────────────────────────────────────────────

/// The opened encrypted local DB. OVERRIDDEN in main() with the real handle —
/// the bare provider throws so a missing override fails loudly in tests/dev.
final appDatabaseProvider = Provider<Database>((ref) {
  throw StateError(
    'appDatabaseProvider must be overridden with an opened Database '
    '(see main.dart / openAppDb).',
  );
});

/// DB health, OVERRIDDEN in main() with the real AppDbResult.health.
final dbHealthProvider = StateProvider<DbHealth>((ref) => const DbHealth.ok());

/// Local task store backed by the encrypted DB.
final taskDaoProvider = Provider<TaskDao>((ref) {
  return TaskDao(ref.watch(appDatabaseProvider));
});

/// Remote seam (Dio-backed). Same transport as before — now only used by the
/// sync engine, never directly by the UI.
final tasksRepositoryProvider = Provider<TasksRepository>((ref) {
  return TasksRepository(DioTasksTransport(ref.watch(apiClientProvider)));
});

/// The offline-first sync engine.
final taskSyncProvider = Provider<TaskSync>((ref) {
  return TaskSync(ref.watch(taskDaoProvider), ref.watch(tasksRepositoryProvider));
});

/// Reachability of the user's own backend (OS link + active host ping).
/// Kept alive for the app lifetime and started lazily.
final reachabilityProvider = Provider<Reachability>((ref) {
  final probe = DefaultConnectivityProbe(ref.watch(apiClientProvider));
  final reach = Reachability(probe);
  ref.onDispose(reach.dispose);
  return reach;
});

/// Whether the backend is reachable right now, as a reactive bool. The Tasks
/// screen watches this to drive the offline banner.
final reachableProvider = StateNotifierProvider<_ReachableNotifier, bool>((ref) {
  final reach = ref.watch(reachabilityProvider);
  return _ReachableNotifier(reach);
});

class _ReachableNotifier extends StateNotifier<bool> {
  final Reachability _reach;
  StreamSubscription<bool>? _sub;

  // Start OPTIMISTIC (true): assume the backend is reachable until the first
  // probe resolves. This avoids both a false "offline" flash at launch AND the
  // boot-time false→true edge that would fire a spurious double-sync. The real
  // value flows in via the stream / the start() resolution below.
  _ReachableNotifier(this._reach) : super(true) {
    _sub = _reach.reachable.listen((v) => state = v);
    // Kick off the initial probe; updates flow back through the stream.
    unawaited(_reach.start().then((_) => state = _reach.value));
  }

  @override
  void dispose() {
    _sub?.cancel();
    super.dispose();
  }
}

// ── State ──────────────────────────────────────────────────────────────────

class TasksState {
  final List<Task> tasks;

  /// Ids with un-pushed local edits — the UI shows a cloud-off badge on these.
  final Set<String> dirtyIds;
  final bool isLoading;
  final String? error;

  const TasksState({
    this.tasks = const [],
    this.dirtyIds = const {},
    this.isLoading = false,
    this.error,
  });

  TasksState copyWith({
    List<Task>? tasks,
    Set<String>? dirtyIds,
    bool? isLoading,
    String? error,
  }) =>
      TasksState(
        tasks: tasks ?? this.tasks,
        dirtyIds: dirtyIds ?? this.dirtyIds,
        isLoading: isLoading ?? this.isLoading,
        error: error,
      );
}

// ── Notifier ───────────────────────────────────────────────────────────────

/// Offline-first tasks notifier.
///
/// Reads come from the local DAO (instant, works offline). Writes go to the
/// DAO + outbox first (optimistic), then a best-effort [TaskSync.sync] pushes
/// them when the backend is reachable. The UI never blocks on the network.
class TasksNotifier extends StateNotifier<TasksState> {
  final TaskDao _dao;
  final TaskSync _sync;

  TasksNotifier(this._dao, this._sync) : super(const TasksState());

  /// Load from the local cache immediately, then kick a background sync and
  /// refresh from cache again when it settles.
  Future<void> load() async {
    state = state.copyWith(isLoading: true, error: null);
    try {
      await _refreshFromCache(loading: false);
    } catch (e) {
      // A degraded/corrupt cache must never strand the screen on the loading
      // skeleton — surface the error and let the UI recover.
      state = state.copyWith(isLoading: false, error: e.toString());
    } finally {
      if (state.isLoading) state = state.copyWith(isLoading: false);
    }
    // Best-effort sync; failures are silent (offline is a normal state).
    unawaited(_syncThenRefresh());
  }

  /// Pull-to-refresh: force a sync then re-read the cache.
  Future<void> refresh() async {
    await _syncThenRefresh();
  }

  /// Trigger a sync (e.g. when reachability flips to true) then refresh.
  Future<void> syncNow() => _syncThenRefresh();

  Future<void> addTask(
    String title, {
    String? priority,
    String? dueDate,
    String? category,
  }) async {
    try {
      await _dao.applyLocalCreate(
        title,
        priority: priority ?? 'medium',
        dueDate: dueDate,
        category: category,
      );
      await _refreshFromCache();
      unawaited(_syncThenRefresh());
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  /// Patch an existing task locally (title / notes / priority / due date /
  /// category). Only the supplied fields change; the rest are preserved. Lands
  /// optimistically in the cache + outbox, then best-effort syncs.
  Future<void> updateTask(
    String id, {
    String? title,
    String? description,
    String? priority,
    String? dueDate,
    String? category,
  }) async {
    try {
      await _dao.applyLocalUpdate(
        id,
        title: title,
        description: description,
        priority: priority,
        dueDate: dueDate,
        category: category,
      );
      await _refreshFromCache();
      unawaited(_syncThenRefresh());
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  Future<void> completeTask(String id) async {
    try {
      await _dao.applyLocalComplete(id);
      await _refreshFromCache();
      unawaited(_syncThenRefresh());
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  Future<void> deleteTask(String id) async {
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

  Future<void> _refreshFromCache({bool loading = false}) async {
    final tasks = await _dao.list();
    final dirty = await _dao.dirtyIds();
    state = TasksState(tasks: tasks, dirtyIds: dirty, isLoading: loading);
  }

  Future<void> _syncThenRefresh() async {
    try {
      await _sync.sync();
    } catch (_) {
      // Offline / server down — the local cache already holds the truth.
    }
    if (!mounted) return;
    try {
      await _refreshFromCache();
    } catch (e) {
      // A cache-read throw here must not escape as an unhandled async error.
      state = state.copyWith(error: e.toString());
    }
  }
}

// ── Provider ───────────────────────────────────────────────────────────────

final tasksProvider =
    StateNotifierProvider<TasksNotifier, TasksState>((ref) {
  final notifier = TasksNotifier(
    ref.watch(taskDaoProvider),
    ref.watch(taskSyncProvider),
  );

  // When the backend comes back online, drain the outbox + pull deltas.
  ref.listen<bool>(reachableProvider, (prev, next) {
    if (next == true && prev != true) {
      notifier.syncNow();
    }
  });

  return notifier;
});
