import '../core/api/api_client.dart';
import '../models/task.dart';

/// Testable seam — mirrors the AuthTransport pattern.
abstract class TasksTransport {
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  });
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body);
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
  Future<Map<String, dynamic>> deleteJson(String path) =>
      _client.delete<Map<String, dynamic>>(
        path,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );
}

class TasksRepository {
  final TasksTransport _t;
  TasksRepository(this._t);

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
  Future<Task> createTask(
    String title, {
    String? description,
    String? category,
    String? priority,
    String? dueDate,
    String? reminderAt,
    String? recurring,
  }) async {
    final body = <String, dynamic>{'title': title};
    if (description != null) body['description'] = description;
    if (category != null) body['category'] = category;
    if (priority != null) body['priority'] = priority;
    if (dueDate != null) body['due_date'] = dueDate;
    if (reminderAt != null) body['reminder_at'] = reminderAt;
    if (recurring != null) body['recurring'] = recurring;

    final json = await _t.postJson('/api/tasks', body);
    // Server wraps: {"task": {...}}
    final raw = json['task'] as Map<String, dynamic>? ?? json;
    return Task.fromJson(raw);
  }

  /// Mark a task done via POST /api/tasks/{id}/complete.
  Future<void> completeTask(String id) async {
    await _t.postJson('/api/tasks/$id/complete', const {});
  }

  /// Delete a task.
  Future<void> deleteTask(String id) async {
    await _t.deleteJson('/api/tasks/$id');
  }
}
