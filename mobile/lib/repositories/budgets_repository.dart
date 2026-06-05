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
  Future<Map<String, dynamic>> patchJson(
      String path, Map<String, dynamic> body);
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
  Future<Map<String, dynamic>> deleteJson(String path) =>
      _client.delete<Map<String, dynamic>>(
        path,
        fromJson: (d) => Map<String, dynamic>.from(d as Map),
      );
}

/// A server project paired with its authoritative `updated_at` — the timestamp
/// last-write-wins compares against. Keeps the immutable [Project] model free of
/// sync-only fields (it doesn't surface `updated_at`).
class ServerProject {
  final Project project;
  final String? updatedAt;
  const ServerProject(this.project, this.updatedAt);
}

/// A server expense paired with its authoritative `updated_at`.
class ServerExpense {
  final Expense expense;
  final String? updatedAt;
  const ServerExpense(this.expense, this.updatedAt);
}

/// One server-side delta page from `GET /api/budgets/changes`. Carries BOTH
/// entities + both tombstone lists + the server `now` (the next shared cursor).
class BudgetChanges {
  /// Projects created/updated server-side since the cursor.
  final List<ServerProject> projects;

  /// Expenses created/updated server-side since the cursor.
  final List<ServerExpense> expenses;

  /// Ids of projects the server soft-deleted since the cursor.
  final List<String> deletedProjects;

  /// Ids of expenses the server soft-deleted since the cursor.
  final List<String> deletedExpenses;

  /// Server "now" timestamp — becomes the next cursor (avoids clock skew).
  final String now;

  const BudgetChanges({
    required this.projects,
    required this.expenses,
    required this.deletedProjects,
    required this.deletedExpenses,
    required this.now,
  });
}

class BudgetsRepository {
  final BudgetsTransport _t;
  BudgetsRepository(this._t);

  /// Pull the delta since [since] (ISO timestamp, null = full snapshot). Maps
  /// `GET /api/budgets/changes?since=<iso>` →
  /// {projects, expenses, deleted_projects, deleted_expenses, now}.
  Future<BudgetChanges> fetchChanges({String? since}) async {
    final json = await _t.getJson(
      '/api/budgets/changes',
      queryParams: since == null ? null : {'since': since},
    );
    final rawProjects = json['projects'] as List? ?? const [];
    final rawExpenses = json['expenses'] as List? ?? const [];
    final rawDelProjects = json['deleted_projects'] as List? ?? const [];
    final rawDelExpenses = json['deleted_expenses'] as List? ?? const [];
    return BudgetChanges(
      projects: rawProjects.map((e) {
        final map = Map<String, dynamic>.from(e as Map);
        return ServerProject(
            Project.fromJson(map), map['updated_at']?.toString());
      }).toList(),
      expenses: rawExpenses.map((e) {
        final map = Map<String, dynamic>.from(e as Map);
        return ServerExpense(
            Expense.fromJson(map), map['updated_at']?.toString());
      }).toList(),
      deletedProjects: rawDelProjects.map((e) => e.toString()).toList(),
      deletedExpenses: rawDelExpenses.map((e) => e.toString()).toList(),
      now: (json['now'] ?? '').toString(),
    );
  }

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

  /// Create an expense under [projectId]. Pass [id] to send a client-minted
  /// UUID — the server's POST accepts it, making the create idempotent on
  /// outbox replay. Returns the created [Expense].
  Future<Expense> createExpense(
    String projectId,
    double amount,
    String description, {
    String? id,
    String? vendor,
  }) async {
    final body = <String, dynamic>{
      'amount': amount,
      'description': description,
    };
    if (id != null) body['id'] = id;
    if (vendor != null) body['vendor'] = vendor;

    final json = await _t.postJson(
      '/api/budgets/projects/$projectId/expenses',
      body,
    );
    final raw = json['expense'] as Map<String, dynamic>? ?? json;
    return Expense.fromJson(raw);
  }

  /// Delete an expense by id (soft delete server-side).
  Future<void> deleteExpense(String id) async {
    await _t.deleteJson('/api/budgets/expenses/$id');
  }

  /// Create a new budget project. Pass [id] to send a client-minted UUID
  /// (idempotent replay). Returns the created [Project].
  Future<Project> createProject(
    String name, {
    String? id,
    double? budget,
    String? description,
  }) async {
    final body = <String, dynamic>{'name': name};
    if (id != null) body['id'] = id;
    if (budget != null) body['budget'] = budget;
    if (description != null) body['description'] = description;

    final json = await _t.postJson('/api/budgets/projects', body);
    final raw = json['project'] as Map<String, dynamic>? ?? json;
    return Project.fromJson(raw);
  }

  /// Patch a project via PATCH /api/budgets/projects/{id}. [patch] holds
  /// snake_case fields.
  Future<void> updateProject(String id, Map<String, dynamic> patch) async {
    await _t.patchJson('/api/budgets/projects/$id', patch);
  }

  /// Delete a project (soft delete server-side).
  Future<void> deleteProject(String id) async {
    await _t.deleteJson('/api/budgets/projects/$id');
  }
}
