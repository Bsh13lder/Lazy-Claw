import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api/api_exceptions.dart';
import '../models/task.dart';
import '../repositories/tasks_repository.dart';
import 'auth_provider.dart';

// ── Repository provider ────────────────────────────────────────────────────

final tasksRepositoryProvider = Provider<TasksRepository>((ref) {
  return TasksRepository(DioTasksTransport(ref.watch(apiClientProvider)));
});

// ── State ──────────────────────────────────────────────────────────────────

class TasksState {
  final List<Task> tasks;
  final bool isLoading;
  final String? error;

  const TasksState({
    this.tasks = const [],
    this.isLoading = false,
    this.error,
  });

  TasksState copyWith({
    List<Task>? tasks,
    bool? isLoading,
    String? error,
  }) =>
      TasksState(
        tasks: tasks ?? this.tasks,
        isLoading: isLoading ?? this.isLoading,
        error: error,
      );
}

// ── Notifier ───────────────────────────────────────────────────────────────

class TasksNotifier extends StateNotifier<TasksState> {
  final TasksRepository _repo;
  TasksNotifier(this._repo) : super(const TasksState());

  Future<void> load() async {
    state = state.copyWith(isLoading: true, error: null);
    try {
      final tasks = await _repo.listTasks();
      state = TasksState(tasks: tasks, isLoading: false);
    } on ApiError catch (e) {
      state = TasksState(error: e.message, isLoading: false);
    } catch (e) {
      state = TasksState(error: e.toString(), isLoading: false);
    }
  }

  Future<void> addTask(
    String title, {
    String? priority,
    String? dueDate,
    String? category,
  }) async {
    try {
      final task = await _repo.createTask(
        title,
        priority: priority,
        dueDate: dueDate,
        category: category,
      );
      // Immutable prepend — do not mutate existing list.
      state = state.copyWith(tasks: [task, ...state.tasks]);
    } on ApiError catch (e) {
      state = state.copyWith(error: e.message);
    } catch (e) {
      state = state.copyWith(error: e.toString());
    }
  }

  Future<void> completeTask(String id) async {
    // Optimistic update: mark done immediately.
    final updated = state.tasks.map((t) {
      if (t.id == id) return t.copyWith(status: 'done');
      return t;
    }).toList();
    state = state.copyWith(tasks: updated);

    try {
      await _repo.completeTask(id);
    } on ApiError catch (e) {
      // Rollback on failure.
      state = state.copyWith(
        tasks: state.tasks.map((t) {
          if (t.id == id) return t.copyWith(status: 'todo');
          return t;
        }).toList(),
        error: e.message,
      );
    } catch (e) {
      state = state.copyWith(
        tasks: state.tasks.map((t) {
          if (t.id == id) return t.copyWith(status: 'todo');
          return t;
        }).toList(),
        error: e.toString(),
      );
    }
  }

  Future<void> deleteTask(String id) async {
    // Optimistic remove.
    final without = state.tasks.where((t) => t.id != id).toList();
    state = state.copyWith(tasks: without);

    try {
      await _repo.deleteTask(id);
    } on ApiError catch (e) {
      // Reload the list to restore the task.
      state = state.copyWith(error: e.message);
      await load();
    } catch (e) {
      state = state.copyWith(error: e.toString());
      await load();
    }
  }

  void clearError() => state = state.copyWith(error: null);
}

// ── Provider ───────────────────────────────────────────────────────────────

final tasksProvider =
    StateNotifierProvider<TasksNotifier, TasksState>((ref) {
  return TasksNotifier(ref.watch(tasksRepositoryProvider));
});
