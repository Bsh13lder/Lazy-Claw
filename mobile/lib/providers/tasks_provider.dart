import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:sqflite_sqlcipher/sqflite.dart';

import '../core/home_widget_tasks.dart';
import '../core/notifications/task_reminder_service.dart';
import '../core/reminder_offset.dart';
import '../local/app_db.dart' show DbHealth;
import '../local/task_dao.dart';
import '../models/comment.dart';
import '../models/subtask.dart';
import '../models/task.dart';
import '../notifications/local_notifications.dart';
import '../notifications/notification_actions.dart';
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
  return TaskSync(
    ref.watch(taskDaoProvider),
    ref.watch(tasksRepositoryProvider),
    // A server tombstone is the only delete that never passes through
    // deleteTask (which cancels alarms itself) — sweep the task's full
    // reserved notification-id range so a remotely-deleted task's local
    // alarms can't keep firing.
    onTaskTombstoned: cancelTaskReminderAlarms,
  );
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
  final prefs = ref.read(settingsPrefsProvider).valueOrNull;
  final service = TaskReminderService(
    FlutterLocalNotificationsSink(LocalNotifications.plugin),
    defaultReminderMinutes: prefs?.defaultReminderMinutes ?? kDefaultReminderMinutes,
    offsets: prefs?.reminderOffsets ?? kDefaultReminderOffsets,
  );
  // Keep the date-only fallback time-of-day in sync with the user's pref.
  ref.listen<int>(
    settingsPrefsProvider.select(
      (s) => s.valueOrNull?.defaultReminderMinutes ?? kDefaultReminderMinutes,
    ),
    (_, next) => service.defaultReminderMinutes = next,
  );
  // Keep the default reminder OFFSET set in sync with the user's multi-select.
  ref.listen<List<String>>(
    settingsPrefsProvider.select(
      (s) => s.valueOrNull?.reminderOffsets ?? kDefaultReminderOffsets,
    ),
    (_, next) => service.offsets = next,
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
final reachableProvider = StateNotifierProvider<_ReachableNotifier, bool>((
  ref,
) {
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
  }) => TasksState(
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
    String? recurring,
    String? recurUntil,
    String? description,
    String? steps,
  }) async {
    try {
      // An empty string means "no reminder" on create — normalise to null so
      // we don't persist a blank reminder_at (empty is only meaningful as a
      // *clear* on update).
      final remAt = (reminderAt != null && reminderAt.isEmpty)
          ? null
          : reminderAt;
      // Likewise an empty cron means "does not repeat" — normalise to null.
      final recur = (recurring != null && recurring.isEmpty) ? null : recurring;
      // An empty series-end means "repeats forever" on create — normalise to
      // null (the '' sentinel is only meaningful as a *clear* on update). A
      // series end without a recurrence is meaningless, so it's dropped too.
      final until = (recurUntil == null || recurUntil.isEmpty || recur == null)
          ? null
          : recurUntil;
      // Empty notes / an empty serialized checklist mean "none" on create —
      // normalise to null so we don't persist a blank column or a literal "[]".
      final desc = (description != null && description.isEmpty)
          ? null
          : description;
      final st = (steps != null && steps.isEmpty) ? null : steps;
      final created = await _dao.applyLocalCreate(
        title,
        priority: priority ?? 'medium',
        dueDate: dueDate,
        category: category,
        reminderAt: remAt,
        recurring: recur,
        recurUntil: until,
        description: desc,
        steps: st,
      );
      await _refreshFromCache();
      unawaited(_reminders?.scheduleForTask(created) ?? Future<void>.value());
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
    String? recurring,
    String? recurUntil,
    String? tags,
    double? allocatedBudget,
    bool clearAllocatedBudget = false,
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
        recurring: recurring,
        recurUntil: recurUntil,
        tags: tags,
        allocatedBudget: allocatedBudget,
        clearAllocatedBudget: clearAllocatedBudget,
      );
      await _refreshFromCache();
      // Reschedule against the new fields (cancels too, if the time was removed
      // or the task is now done). Falls back to a cancel when the row vanished.
      if (updated != null) {
        unawaited(_reminders?.scheduleForTask(updated) ?? Future<void>.value());
      } else {
        unawaited(_reminders?.cancelForTask(id) ?? Future<void>.value());
      }
      unawaited(_syncThenRefresh());
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  /// Reschedule a *set* of tasks to the same target in one batch — the
  /// "Reschedule all" overdue flow. Applies `dueDate` (date-only) + `reminderAt`
  /// to every id, then does ONE cache refresh + ONE best-effort sync (instead of
  /// the N refresh/sync churn N separate [updateTask] calls would cause).
  ///
  /// Each id's local update lands optimistically in the cache + outbox; per-task
  /// local notifications are rescheduled against the new time. Mirrors
  /// [updateTask]'s error handling — a DAO throw surfaces as [TasksState.error]
  /// rather than escaping.
  Future<void> rescheduleMany(
    List<String> ids, {
    required String dueDate,
    required String reminderAt,
  }) async {
    if (ids.isEmpty) return;
    try {
      // Apply every local update first — no per-item refresh, so the cache is
      // re-read exactly once below.
      final updated = <Task>[];
      for (final id in ids) {
        final t = await _dao.applyLocalUpdate(
          id,
          dueDate: dueDate,
          reminderAt: reminderAt,
        );
        if (t != null) updated.add(t);
      }
      await _refreshFromCache();
      // Reschedule each task's local notification against the new reminder time.
      for (final t in updated) {
        unawaited(_reminders?.scheduleForTask(t) ?? Future<void>.value());
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

  /// Append a comment (author=user) optimistically + queue the server append.
  Future<void> addComment(String taskId, String text,
      {String? subtaskId}) async {
    final trimmed = text.trim();
    if (trimmed.isEmpty) return;
    try {
      final comment = TaskComment(
        id: newCommentId(),
        ts: DateTime.now().toUtc().toIso8601String(),
        author: 'user',
        text: trimmed,
        subtaskId: subtaskId,
      );
      await _dao.applyLocalAddComment(taskId, comment);
      await _refreshFromCache();
      unawaited(_syncThenRefresh());
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  Future<void> deleteComment(String taskId, String commentId) async {
    try {
      await _dao.applyLocalDeleteComment(taskId, commentId);
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
      unawaited(_reminders?.cancelForTask(id) ?? Future<void>.value());
      unawaited(_syncThenRefresh());
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  Future<void> deleteTask(String id) async {
    // Optimistically drop the task from state SYNCHRONOUSLY — before any await —
    // so a row's Dismissible.onDismissed never leaves a dismissed tile in the
    // tree. That race throws "A dismissed Dismissible widget is still part of
    // the tree" and blacks out the Tasks screen. The DB delete, reminder cancel,
    // and sync follow; _refreshFromCache re-reconciles from the source of truth.
    state = state.copyWith(
      tasks: state.tasks.where((t) => t.id != id).toList(),
    );
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
      return;
    }
    // Reconcile the whole scheduled reminder set against what the pull just
    // landed. A sync is the ONLY way a task the client never created enters the
    // cache — the backend-minted next occurrence of a recurring task (a brand
    // new id, spawned by complete_task), a task added on web/Telegram, or a
    // reminder edited on another device. Those rows never pass through
    // add/update/complete, so the per-task scheduleForTask hooks miss them
    // entirely and only load() reconciled the set — leaving a respawned
    // recurring task with NO local notification until the next cold start.
    //
    // syncAll is idempotent reconciliation (cancels stale, (re)schedules live)
    // and runs only after a network sync, not on every local mutation.
    unawaited(_reminders?.syncAll(state.tasks) ?? Future<void>.value());
  }
}

// ── Provider ───────────────────────────────────────────────────────────────

final tasksProvider = StateNotifierProvider<TasksNotifier, TasksState>((ref) {
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
