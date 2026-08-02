import '../core/api/api_client.dart';
import '../models/task.dart';

/// Testable seam — mirrors the AuthTransport pattern.
abstract class TasksTransport {
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  });
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body);
  Future<Map<String, dynamic>> patchJson(
      String path, Map<String, dynamic> body);
  Future<Map<String, dynamic>> putJson(String path, Map<String, dynamic> body);
  Future<Map<String, dynamic>> deleteJson(String path);
}

class DioTasksTransport implements TasksTransport {
  final ApiClient _client;
  DioTasksTransport(this._client);

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) =>
      _client.get<Map<String, dynamic>>(
        path,
        queryParams: queryParams,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );

  @override
  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  ) =>
      _client.post<Map<String, dynamic>>(
        path,
        data: body,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );

  @override
  Future<Map<String, dynamic>> patchJson(
    String path,
    Map<String, dynamic> body,
  ) =>
      _client.patch<Map<String, dynamic>>(
        path,
        data: body,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );

  @override
  Future<Map<String, dynamic>> putJson(
    String path,
    Map<String, dynamic> body,
  ) =>
      _client.put<Map<String, dynamic>>(
        path,
        data: body,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );

  @override
  Future<Map<String, dynamic>> deleteJson(String path) =>
      _client.delete<Map<String, dynamic>>(
        path,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );
}

/// A server task paired with its authoritative `updated_at` — the timestamp
/// last-write-wins compares against. Keeps the immutable [Task] model free of
/// sync-only fields.
class ServerTask {
  final Task task;
  final String? updatedAt;
  const ServerTask(this.task, this.updatedAt);
}

/// One server-side delta page from `GET /api/tasks/changes`.
class TaskChanges {
  /// Tasks created/updated server-side since the cursor (with their server
  /// `updated_at` for LWW).
  final List<ServerTask> tasks;

  /// Ids the server soft-deleted since the cursor.
  final List<String> deleted;

  /// Server "now" timestamp — becomes the next cursor (avoids clock skew).
  final String now;

  const TaskChanges({
    required this.tasks,
    required this.deleted,
    required this.now,
  });
}

class TasksRepository {
  final TasksTransport _t;
  TasksRepository(this._t);

  /// Pull the delta since [since] (ISO timestamp, null = full snapshot).
  /// Maps `GET /api/tasks/changes?since=<iso>` → {tasks, deleted, now}.
  Future<TaskChanges> fetchChanges({String? since}) async {
    final json = await _t.getJson(
      '/api/tasks/changes',
      queryParams: since == null ? null : {'since': since},
    );
    final rawTasks = json['tasks'] as List? ?? const [];
    final rawDeleted = json['deleted'] as List? ?? const [];
    return TaskChanges(
      tasks: rawTasks.map((e) {
        final map = Map<String, dynamic>.from(e as Map);
        final updatedAt = map['updated_at']?.toString();
        return ServerTask(Task.fromJson(map), updatedAt);
      }).toList(),
      deleted: rawDeleted.map((e) => e.toString()).toList(),
      now: (json['now'] ?? '').toString(),
    );
  }

  /// Fetch tasks. Optionally filter by owner / status / bucket.
  Future<List<Task>> listTasks({
    String? owner,
    String? status,
    String? bucket,
  }) async {
    final params = <String, dynamic>{};
    if (owner != null) params['owner'] = owner;
    if (status != null) params['status'] = status;
    if (bucket != null) params['bucket'] = bucket;

    final json = await _t.getJson(
      '/api/tasks',
      queryParams: params.isEmpty ? null : params,
    );
    final rawList = json['tasks'] as List? ?? [];
    return rawList
        .map((e) => Task.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  /// Create a task. Returns the created [Task].
  ///
  /// Pass [id] to send a client-minted UUID — the server's POST /api/tasks
  /// accepts it, making the create idempotent on outbox replay.
  Future<Task> createTask(
    String title, {
    String? id,
    String? description,
    String? category,
    String? priority,
    String? dueDate,
    String? reminderAt,
    String? recurring,
    String? recurUntil,
    List<Map<String, dynamic>>? steps,
  }) async {
    final body = <String, dynamic>{'title': title};
    if (id != null) body['id'] = id;
    if (description != null) body['description'] = description;
    if (category != null) body['category'] = category;
    if (priority != null) body['priority'] = priority;
    if (dueDate != null) body['due_date'] = dueDate;
    if (reminderAt != null) body['reminder_at'] = reminderAt;
    if (recurring != null) body['recurring'] = recurring;
    if (recurUntil != null) body['recur_until'] = recurUntil;
    // The initial sub-task checklist rides POST /api/tasks (CreateTaskBody
    // accepts `steps: list[StepDraft]`), so a task created with sub-tasks
    // lands them in one round-trip — no post-create PUT /steps needed.
    if (steps != null) body['steps'] = steps;

    final json = await _t.postJson('/api/tasks', body);
    // Server wraps: {"task": {...}}
    final raw = json['task'] as Map<String, dynamic>? ?? json;
    return Task.fromJson(raw);
  }

  /// Patch a task via PATCH /api/tasks/{id}. [patch] holds snake_case fields.
  Future<void> updateTask(String id, Map<String, dynamic> patch) async {
    await _t.patchJson('/api/tasks/$id', patch);
  }

  /// Replace the full sub-task checklist via PUT /api/tasks/{id}/steps.
  ///
  /// PATCH /api/tasks/{id} (UpdateTaskBody) does NOT accept `steps`, so the
  /// sub-task list rides this dedicated route instead. [steps] is the canonical
  /// `[{id?, title, done}]` shape the server's `SetStepsBody` expects.
  Future<void> setSteps(String id, List<Map<String, dynamic>> steps) async {
    await _t.putJson('/api/tasks/$id/steps', {'steps': steps});
  }

  /// Mark a task done via POST /api/tasks/{id}/complete.
  Future<void> completeTask(String id) async {
    await _t.postJson('/api/tasks/$id/complete', const {});
  }

  /// Delete a task.
  Future<void> deleteTask(String id) async {
    await _t.deleteJson('/api/tasks/$id');
  }

  /// Append a comment via POST /api/tasks/{id}/comments. [body] carries
  /// {id, text, subtask_id?} — the server is idempotent on a replayed client id.
  Future<Map<String, dynamic>> addComment(
          String taskId, Map<String, dynamic> body) =>
      _t.postJson('/api/tasks/$taskId/comments', body);

  /// Delete a comment via DELETE /api/tasks/{id}/comments/{comment_id}
  /// (idempotent server-side).
  Future<void> deleteComment(String taskId, String commentId) async {
    await _t.deleteJson('/api/tasks/$taskId/comments/$commentId');
  }
}
