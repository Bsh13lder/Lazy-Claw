import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:sqflite_sqlcipher/sqflite.dart';

import '../core/home_widget_tasks.dart';
import '../core/notifications/task_reminder_service.dart';
import '../local/app_db.dart' show DbHealth;
import '../local/task_dao.dart';
import '../models/subtask.dart';
import '../models/task.dart';
import '../notifications/local_notifications.dart';
import '../repositories/tasks_repository.dart';
import '../screens/settings/settings_prefs.dart';
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

/// Schedules local notifications at each task's due / reminder time. Reuses the
/// app's single [FlutterLocalNotificationsPlugin] instance.
///
/// The service is kept STABLE (constructed once) so it can't dispose the tasks
/// notifier that watches it. The user's "Default reminder time" pref — the
/// time-of-day at which DATE-ONLY tasks fire — is pushed into the existing
/// instance in-place via `ref.listen`, seeded from the already-loaded pref so a
/// date-only task ALWAYS gets a future reminder, even before the pref resolves
/// (the service defaults to [kDefaultReminderMinutes] = 09:00 internally).
final taskReminderServiceProvider = Provider<TaskReminderScheduler>((ref) {
  final service = TaskReminderService(
    LocalNotifications.plugin,
    defaultReminderMinutes:
        ref.read(settingsPrefsProvider).valueOrNull?.defaultReminderMinutes ??
            kDefaultReminderMinutes,
  );
  // Keep the date-only fallback time-of-day in sync with the user's pref.
  ref.listen<int>(
    settingsPrefsProvider.select(
      (s) => s.valueOrNull?.defaultReminderMinutes ?? kDefaultReminderMinutes,
    ),
    (_, next) => service.defaultReminderMinutes = next,
  );
  return service;
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

  /// Optional local-notification scheduler. Null in tests / when notifications
  /// aren't wired — every call site guards with `?.`, so scheduling is a no-op
  /// rather than a failure.
  final TaskReminderScheduler? _reminders;

  TasksNotifier(this._dao, this._sync, [this._reminders])
      : super(const TasksState());

  /// Load from the local cache immediately, then kick a background sync and
  /// refresh from cache again when it settles.
  Future<void> load() async {
    state = state.copyWith(isLoading: true, error: null);
    try {
      await _refreshFromCache(loading: false);
      // (Re)schedule reminders for everything already in the cache, so a fresh
      // app start picks up due/reminder times for existing tasks and clears any
      // stale schedules. Best-effort — never blocks the load.
      unawaited(_reminders?.syncAll(state.tasks) ?? Future<void>.value());
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
    String? reminderAt,
  }) async {
    try {
      // An empty string means "no reminder" on create — normalise to null so
      // we don't persist a blank reminder_at (empty is only meaningful as a
      // *clear* on update).
      final remAt =
          (reminderAt != null && reminderAt.isEmpty) ? null : reminderAt;
      final created = await _dao.applyLocalCreate(
        title,
        priority: priority ?? 'medium',
        dueDate: dueDate,
        category: category,
        reminderAt: remAt,
      );
      await _refreshFromCache();
      unawaited(
          _reminders?.scheduleForTask(created) ?? Future<void>.value());
      unawaited(_syncThenRefresh());
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  /// Patch an existing task locally (title / notes / priority / due date /
  /// category / sub-task steps). Only the supplied fields change; the rest are
  /// preserved. Lands optimistically in the cache + outbox, then best-effort
  /// syncs.
  ///
  /// [steps] is the serialized `[{id,title,done}]` JSON string from
  /// [serializeSubtasks] — pass it to persist sub-task edits (or via
  /// [setSubtasks] for a typed list).
  Future<void> updateTask(
    String id, {
    String? title,
    String? description,
    String? priority,
    String? dueDate,
    String? category,
    String? steps,
    String? reminderAt,
  }) async {
    try {
      final updated = await _dao.applyLocalUpdate(
        id,
        title: title,
        description: description,
        priority: priority,
        dueDate: dueDate,
        category: category,
        steps: steps,
        reminderAt: reminderAt,
      );
      await _refreshFromCache();
      // Reschedule against the new fields (cancels too, if the time was removed
      // or the task is now done). Falls back to a cancel when the row vanished.
      if (updated != null) {
        unawaited(
            _reminders?.scheduleForTask(updated) ?? Future<void>.value());
      } else {
        unawaited(_reminders?.cancelForTask(id) ?? Future<void>.value());
      }
      unawaited(_syncThenRefresh());
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  /// Persist a typed list of sub-tasks for [id]. Serialises to the canonical
  /// `[{id,title,done}]` JSON string and routes through [updateTask] so it
  /// lands optimistically in the cache + outbox like any other field. An empty
  /// list clears the `steps` column.
  Future<void> setSubtasks(String id, List<Subtask> subtasks) =>
      updateTask(id, steps: serializeSubtasks(subtasks) ?? '');

  Future<void> completeTask(String id) async {
    try {
      await _dao.applyLocalComplete(id);
      await _refreshFromCache();
      unawaited(_reminders?.cancelForTask(id) ?? Future<void>.value());
      unawaited(_syncThenRefresh());
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  Future<void> deleteTask(String id) async {
    try {
      await _dao.applyLocalDelete(id);
      await _refreshFromCache();
      unawaited(_reminders?.cancelForTask(id) ?? Future<void>.value());
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
    // Refresh the "Today tasks" home-screen widget snapshot off the same funnel
    // every mutation (load / add / update / complete / delete) routes through.
    // Fire-and-forget + internally guarded, so it never blocks or breaks a write.
    unawaited(updateTasksWidget(tasks));
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
    ref.watch(taskReminderServiceProvider),
  );

  // When the backend comes back online, drain the outbox + pull deltas.
  ref.listen<bool>(reachableProvider, (prev, next) {
    if (next == true && prev != true) {
      notifier.syncNow();
    }
  });

  return notifier;
});
