import '../core/api/api_client.dart';
import '../models/expense.dart';
import '../models/project.dart';

/// Testable seam — mirrors the TasksTransport pattern.
abstract class BudgetsTransport {
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  });
  Future<Map<String, dynamic>> postJson(String path, Map<String, dynamic> body);
  Future<Map<String, dynamic>> deleteJson(String path);
}

class DioBudgetsTransport implements BudgetsTransport {
  final ApiClient _client;
  DioBudgetsTransport(this._client);

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

class BudgetsRepository {
  final BudgetsTransport _t;
  BudgetsRepository(this._t);

  /// Fetch projects. Optionally filter by status (active|archived|all).
  Future<List<Project>> listProjects({String? status}) async {
    final params = <String, dynamic>{};
    if (status != null) params['status'] = status;

    final json = await _t.getJson(
      '/api/budgets/projects',
      queryParams: params.isEmpty ? null : params,
    );
    final rawList = json['projects'] as List? ?? [];
    return rawList
        .map((e) => Project.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  /// Fetch expenses across all projects, optionally filtered by [projectId].
  /// Uses `GET /api/budgets/expenses` with optional `project_id` query param.
  Future<List<Expense>> listExpenses({String? projectId}) async {
    final params = <String, dynamic>{};
    if (projectId != null) params['project_id'] = projectId;

    final json = await _t.getJson(
      '/api/budgets/expenses',
      queryParams: params.isEmpty ? null : params,
    );
    final rawList = json['expenses'] as List? ?? [];
    return rawList
        .map((e) => Expense.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  /// Create an expense under [projectId].
  /// Returns the created [Expense].
  Future<Expense> createExpense(
    String projectId,
    double amount,
    String description, {
    String? vendor,
  }) async {
    final body = <String, dynamic>{
      'amount': amount,
      'description': description,
    };
    if (vendor != null) body['vendor'] = vendor;

    final json = await _t.postJson(
      '/api/budgets/projects/$projectId/expenses',
      body,
    );
    final raw = json['expense'] as Map<String, dynamic>? ?? json;
    return Expense.fromJson(raw);
  }

  /// Delete an expense by id.
  Future<void> deleteExpense(String id) async {
    await _t.deleteJson('/api/budgets/expenses/$id');
  }

  /// Create a new budget project. Returns the created [Project].
  Future<Project> createProject(String name, {double? budget}) async {
    final body = <String, dynamic>{'name': name};
    if (budget != null) body['budget'] = budget;

    final json = await _t.postJson('/api/budgets/projects', body);
    final raw = json['project'] as Map<String, dynamic>? ?? json;
    return Project.fromJson(raw);
  }
}
