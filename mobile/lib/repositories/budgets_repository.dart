import '../core/api/api_client.dart';
import '../models/budget_entry.dart';
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

/// A server budget-ledger entry paired with its authoritative `updated_at` —
/// the timestamp last-write-wins compares against. Keeps [BudgetEntry] free of
/// sync-only fields (it surfaces only `created_at`).
class ServerBudgetEntry {
  final BudgetEntry entry;
  final String? updatedAt;
  const ServerBudgetEntry(this.entry, this.updatedAt);
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

  /// Budget-ledger entries (top-ups) created/updated server-side since the
  /// cursor. Defaulted so older construction sites stay valid.
  final List<ServerBudgetEntry> budgetEntries;

  /// Ids of ledger entries the server soft-deleted since the cursor.
  final List<String> deletedBudgetEntries;

  /// Server "now" timestamp — becomes the next cursor (avoids clock skew).
  final String now;

  const BudgetChanges({
    required this.projects,
    required this.expenses,
    required this.deletedProjects,
    required this.deletedExpenses,
    this.budgetEntries = const [],
    this.deletedBudgetEntries = const [],
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
    final rawEntries = json['budget_entries'] as List? ?? const [];
    final rawDelEntries = json['deleted_budget_entries'] as List? ?? const [];
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
      budgetEntries: rawEntries.map((e) {
        final map = Map<String, dynamic>.from(e as Map);
        return ServerBudgetEntry(
            BudgetEntry.fromJson(map), map['updated_at']?.toString());
      }).toList(),
      deletedBudgetEntries:
          rawDelEntries.map((e) => e.toString()).toList(),
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
    String? currency,
    String? spentAt,
    String? notes,
  }) async {
    final body = <String, dynamic>{
      'amount': amount,
      'description': description,
    };
    if (id != null) body['id'] = id;
    if (vendor != null) body['vendor'] = vendor;
    if (currency != null) body['currency'] = currency;
    if (spentAt != null) body['spent_at'] = spentAt;
    if (notes != null) body['notes'] = notes;

    final json = await _t.postJson(
      '/api/budgets/projects/$projectId/expenses',
      body,
    );
    final raw = json['expense'] as Map<String, dynamic>? ?? json;
    return Expense.fromJson(raw);
  }

  /// Patch an expense via PATCH /api/budgets/expenses/{id}. [patch] holds
  /// snake_case fields (amount/description/vendor/project_id/notes/spent_at).
  Future<void> updateExpense(String id, Map<String, dynamic> patch) async {
    await _t.patchJson('/api/budgets/expenses/$id', patch);
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
    String? color,
  }) async {
    final body = <String, dynamic>{'name': name};
    if (id != null) body['id'] = id;
    if (budget != null) body['budget'] = budget;
    if (description != null) body['description'] = description;
    if (color != null) body['color'] = color;

    final json = await _t.postJson('/api/budgets/projects', body);
    final raw = json['project'] as Map<String, dynamic>? ?? json;
    return Project.fromJson(raw);
  }

  /// Patch a project via PATCH /api/budgets/projects/{id}. [patch] holds
  /// snake_case fields.
  Future<void> updateProject(String id, Map<String, dynamic> patch) async {
    await _t.patchJson('/api/budgets/projects/$id', patch);
  }

  /// Delete a project (soft delete server-side). Always passes `cascade=true`:
  /// the server returns 409 for a delete of a project that still has expenses,
  /// and the client's own delete UX already warns the user that the project
  /// "and all its expenses will be removed". Without the flag a project-with-
  /// expenses delete would 409, get drained from the outbox, and leave the row
  /// tombstoned-but-unsynced (re-appearing on the next full pull).
  Future<void> deleteProject(String id) async {
    await _t.deleteJson('/api/budgets/projects/$id?cascade=true');
  }

  // ── Budget ledger (online-only — no offline cache) ─────────────────────────
  //
  // The `budget_entries` ledger mirrors the web "+ Add budget" / "📋 Log"
  // controls. These hit the live backend directly (NOT the offline sync table):
  // a sourced top-up bumps `projects.budget`, and an edit/delete adjusts the
  // total by the amount delta. Callers refresh the budgets provider afterwards
  // so the (offline-cached) project budget bar reflects the new total.

  /// List a project's budget ledger entries (top-ups + edit audits), newest
  /// first. Maps `GET /api/budgets/projects/{projectId}/budget-entries` →
  /// `{entries: [...], count}`.
  Future<List<BudgetEntry>> listBudgetEntries(String projectId) async {
    final json = await _t.getJson(
      '/api/budgets/projects/$projectId/budget-entries',
    );
    final rawList = json['entries'] as List? ?? [];
    return rawList
        .map((e) => BudgetEntry.fromJson(Map<String, dynamic>.from(e as Map)))
        .toList();
  }

  /// Add money to a project's budget, recording WHERE it came from. The server
  /// bumps `projects.budget` by [amount] and writes a `credit` ledger row.
  /// [source] is the first comment (the answer to "where is this from?").
  /// Maps `POST /api/budgets/projects/{projectId}/budget-entries` → `{entry}`.
  Future<BudgetEntry> addBudgetEntry(
    String projectId,
    double amount, {
    String? id,
    String? source,
    String? currency,
  }) async {
    final body = <String, dynamic>{'amount': amount};
    if (id != null) body['id'] = id;
    if (source != null) body['source'] = source;
    if (currency != null) body['currency'] = currency;

    final json = await _t.postJson(
      '/api/budgets/projects/$projectId/budget-entries',
      body,
    );
    final raw = json['entry'] as Map<String, dynamic>? ?? json;
    return BudgetEntry.fromJson(raw);
  }

  /// Edit a ledger entry. The server adjusts `projects.budget` by the amount
  /// delta so the total stays consistent (entry was +200, edit to +150 →
  /// budget -= 50). Only supplied fields change. Maps
  /// `PATCH /api/budgets/entries/{id}` → `{entry}`.
  Future<BudgetEntry> updateBudgetEntry(
    String id, {
    double? amount,
    String? source,
    String? currency,
  }) async {
    final body = <String, dynamic>{};
    if (amount != null) body['amount'] = amount;
    if (source != null) body['source'] = source;
    if (currency != null) body['currency'] = currency;

    final json = await _t.patchJson('/api/budgets/entries/$id', body);
    final raw = json['entry'] as Map<String, dynamic>? ?? json;
    return BudgetEntry.fromJson(raw);
  }

  /// Delete a ledger entry, rolling back its effect on `projects.budget`.
  /// Maps `DELETE /api/budgets/entries/{id}`.
  Future<void> deleteBudgetEntry(String id) async {
    await _t.deleteJson('/api/budgets/entries/$id');
  }
}
