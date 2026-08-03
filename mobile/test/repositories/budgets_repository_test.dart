import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/repositories/budgets_repository.dart';
import 'package:lazyclaw_mobile/models/budget_entry.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/models/expense.dart';

// ── Fake transport ─────────────────────────────────────────────────────────

class _FakeTransport implements BudgetsTransport {
  String? lastMethod;
  String? lastPath;
  Map<String, dynamic>? lastBody;
  Map<String, dynamic>? lastQueryParams;

  Map<String, dynamic> response;

  _FakeTransport(this.response);

  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async {
    lastMethod = 'GET';
    lastPath = path;
    lastQueryParams = queryParams;
    return response;
  }

  @override
  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    lastMethod = 'POST';
    lastPath = path;
    lastBody = body;
    return response;
  }

  @override
  Future<Map<String, dynamic>> patchJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    lastMethod = 'PATCH';
    lastPath = path;
    lastBody = body;
    return response;
  }

  @override
  Future<Map<String, dynamic>> deleteJson(String path) async {
    lastMethod = 'DELETE';
    lastPath = path;
    return response;
  }
}

// ── Fixtures ───────────────────────────────────────────────────────────────

Map<String, dynamic> _projectJson({
  String id = 'proj1',
  String name = 'Test Project',
  double budget = 1000.0,
  double spent = 200.0,
}) =>
    {
      'id': id,
      'name': name,
      'name_key': name.toLowerCase(),
      'budget': budget,
      'currency': 'USD',
      'status': 'active',
      'description': null,
      'lazybrain_note_id': null,
      'spent': spent,
      'remaining': budget - spent,
    };

Map<String, dynamic> _expenseJson({
  String id = 'exp1',
  String projectId = 'proj1',
  double amount = 50.0,
  String description = 'Test expense',
}) =>
    {
      'id': id,
      'project_id': projectId,
      'task_id': null,
      'amount': amount,
      'currency': 'USD',
      'description': description,
      'vendor': null,
      'notes': null,
      'spent_at': '2026-06-01T10:00:00Z',
      'status': 'posted',
      'recurring_expense_id': null,
      'lazybrain_note_id': null,
    };

Map<String, dynamic> _entryJson({
  String id = 'be1',
  String projectId = 'proj1',
  double amount = 100.0,
  String currency = 'USD',
  String? source,
  String kind = 'credit',
}) =>
    {
      'id': id,
      'project_id': projectId,
      'amount': amount,
      'currency': currency,
      'source': source,
      'kind': kind,
      'created_at': '2026-06-05T10:00:00Z',
    };

// ── Tests ──────────────────────────────────────────────────────────────────

void main() {
  group('BudgetsRepository.listProjects', () {
    test('GET /api/budgets/projects and parses project list', () async {
      final t = _FakeTransport({
        'projects': [
          _projectJson(id: 'p1', name: 'Alpha'),
          _projectJson(id: 'p2', name: 'Beta'),
        ],
        'count': 2,
      });
      final repo = BudgetsRepository(t);
      final projects = await repo.listProjects();
      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/budgets/projects');
      expect(projects, hasLength(2));
      expect(projects[0].id, 'p1');
      expect(projects[1].name, 'Beta');
    });

    test('passes status query param', () async {
      final t = _FakeTransport({'projects': [], 'count': 0});
      await BudgetsRepository(t).listProjects(status: 'active');
      expect(t.lastQueryParams, containsPair('status', 'active'));
    });

    test('sends no queryParams when status is null', () async {
      final t = _FakeTransport({'projects': [], 'count': 0});
      await BudgetsRepository(t).listProjects();
      expect(t.lastQueryParams, isNull);
    });

    test('returns empty list when projects key is missing', () async {
      final t = _FakeTransport({});
      final projects = await BudgetsRepository(t).listProjects();
      expect(projects, isEmpty);
    });

    test('parses spent and remaining from list response', () async {
      final t = _FakeTransport({
        'projects': [_projectJson(budget: 500.0, spent: 250.0)],
        'count': 1,
      });
      final projects = await BudgetsRepository(t).listProjects();
      expect(projects[0].spent, 250.0);
      expect(projects[0].remaining, 250.0);
    });
  });

  group('BudgetsRepository.listExpenses', () {
    test('GET /api/budgets/expenses and parses expense list', () async {
      final t = _FakeTransport({
        'expenses': [
          _expenseJson(id: 'e1', amount: 10.0),
          _expenseJson(id: 'e2', amount: 20.0),
        ],
        'count': 2,
      });
      final repo = BudgetsRepository(t);
      final expenses = await repo.listExpenses();
      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/budgets/expenses');
      expect(expenses, hasLength(2));
      expect(expenses[0].id, 'e1');
      expect(expenses[1].amount, 20.0);
    });

    test('passes projectId as query param when provided', () async {
      final t = _FakeTransport({'expenses': [], 'count': 0});
      await BudgetsRepository(t).listExpenses(projectId: 'proj1');
      expect(t.lastPath, '/api/budgets/expenses');
      expect(t.lastQueryParams, containsPair('project_id', 'proj1'));
    });

    test('sends no queryParams when projectId is null', () async {
      final t = _FakeTransport({'expenses': [], 'count': 0});
      await BudgetsRepository(t).listExpenses();
      expect(t.lastQueryParams, isNull);
    });

    test('returns empty list when expenses key is missing', () async {
      final t = _FakeTransport({});
      final expenses = await BudgetsRepository(t).listExpenses();
      expect(expenses, isEmpty);
    });
  });

  group('BudgetsRepository.createExpense', () {
    test('POST /api/budgets/projects/{id}/expenses with required fields', () async {
      final t = _FakeTransport({'expense': _expenseJson(id: 'new1')});
      final repo = BudgetsRepository(t);
      final expense = await repo.createExpense('proj1', 75.0, 'Lunch');
      expect(t.lastMethod, 'POST');
      expect(t.lastPath, '/api/budgets/projects/proj1/expenses');
      expect(t.lastBody, containsPair('amount', 75.0));
      expect(t.lastBody, containsPair('description', 'Lunch'));
      expect(expense, isA<Expense>());
      expect(expense.id, 'new1');
    });

    test('includes vendor when provided', () async {
      final t = _FakeTransport({'expense': _expenseJson()});
      await BudgetsRepository(t).createExpense(
        'proj1',
        99.0,
        'Cloud hosting',
        vendor: 'AWS',
      );
      expect(t.lastBody, containsPair('vendor', 'AWS'));
    });

    test('does not include vendor when null', () async {
      final t = _FakeTransport({'expense': _expenseJson()});
      await BudgetsRepository(t).createExpense('proj1', 10.0, 'Item');
      expect(t.lastBody?.containsKey('vendor'), isFalse);
    });

    // The server's CreateExpenseBody already accepts task_id/subtask_id (and
    // enforces "subtask_id implies task_id" with a 400) — these prove the
    // client actually sends them so an expense can be born linked.
    test('includes task_id + subtask_id when provided', () async {
      final t = _FakeTransport({'expense': _expenseJson()});
      await BudgetsRepository(t).createExpense(
        'proj1',
        20.0,
        'Paint',
        taskId: 't1',
        subtaskId: 's1',
      );
      expect(t.lastBody, containsPair('task_id', 't1'));
      expect(t.lastBody, containsPair('subtask_id', 's1'));
    });

    test('omits task_id / subtask_id entirely when null', () async {
      final t = _FakeTransport({'expense': _expenseJson()});
      await BudgetsRepository(t).createExpense('proj1', 10.0, 'Item');
      expect(t.lastBody?.containsKey('task_id'), isFalse);
      expect(t.lastBody?.containsKey('subtask_id'), isFalse);
    });
  });

  group('BudgetsRepository.deleteExpense', () {
    test('DELETE /api/budgets/expenses/{id}', () async {
      final t = _FakeTransport({'status': 'deleted'});
      await BudgetsRepository(t).deleteExpense('exp99');
      expect(t.lastMethod, 'DELETE');
      expect(t.lastPath, '/api/budgets/expenses/exp99');
    });
  });

  group('BudgetsRepository.createProject', () {
    test('POST /api/budgets/projects with name and returns Project', () async {
      final t = _FakeTransport({'project': _projectJson(id: 'new_proj', name: 'New Project')});
      final repo = BudgetsRepository(t);
      final project = await repo.createProject('New Project');
      expect(t.lastMethod, 'POST');
      expect(t.lastPath, '/api/budgets/projects');
      expect(t.lastBody, containsPair('name', 'New Project'));
      expect(project, isA<Project>());
      expect(project.id, 'new_proj');
    });

    test('includes budget when provided', () async {
      final t = _FakeTransport({'project': _projectJson()});
      await BudgetsRepository(t).createProject('Funded', budget: 500.0);
      expect(t.lastBody, containsPair('budget', 500.0));
    });

    test('does not include budget when null', () async {
      final t = _FakeTransport({'project': _projectJson()});
      await BudgetsRepository(t).createProject('Unfunded');
      expect(t.lastBody?.containsKey('budget'), isFalse);
    });

    test('sends a client id when provided (idempotent replay)', () async {
      final t = _FakeTransport({'project': _projectJson()});
      await BudgetsRepository(t).createProject('Pinned', id: 'client-uuid');
      expect(t.lastBody, containsPair('id', 'client-uuid'));
    });
  });

  group('BudgetsRepository.createExpense client id', () {
    test('sends a client id when provided (idempotent replay)', () async {
      final t = _FakeTransport({'expense': _expenseJson()});
      await BudgetsRepository(t)
          .createExpense('proj1', 5.0, 'Item', id: 'client-exp');
      expect(t.lastBody, containsPair('id', 'client-exp'));
    });
  });

  group('BudgetsRepository.updateProject / deleteProject', () {
    test('PATCH /api/budgets/projects/{id} with the patch', () async {
      final t = _FakeTransport({'project': _projectJson()});
      await BudgetsRepository(t)
          .updateProject('proj1', {'name': 'Renamed', 'budget': 200.0});
      expect(t.lastMethod, 'PATCH');
      expect(t.lastPath, '/api/budgets/projects/proj1');
      expect(t.lastBody, containsPair('name', 'Renamed'));
    });

    test('DELETE /api/budgets/projects/{id} passes cascade=true', () async {
      // The delete UX warns "and all its expenses will be removed", and the
      // server 409s a non-cascade delete of a project that still has expenses,
      // so the client must always cascade.
      final t = _FakeTransport({'status': 'deleted'});
      await BudgetsRepository(t).deleteProject('proj9');
      expect(t.lastMethod, 'DELETE');
      expect(t.lastPath, '/api/budgets/projects/proj9?cascade=true');
    });
  });

  group('BudgetsRepository.fetchChanges', () {
    test('GET /api/budgets/changes parses both entities + tombstones + now',
        () async {
      final t = _FakeTransport({
        'projects': [_projectJson(id: 'p1')],
        'expenses': [_expenseJson(id: 'e1')],
        'deleted_projects': ['dp1'],
        'deleted_expenses': ['de1'],
        'now': '2026-06-05T12:00:00Z',
      });
      final changes = await BudgetsRepository(t).fetchChanges();
      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/budgets/changes');
      expect(changes.projects, hasLength(1));
      expect(changes.projects.first.project.id, 'p1');
      expect(changes.expenses, hasLength(1));
      expect(changes.expenses.first.expense.id, 'e1');
      expect(changes.deletedProjects, ['dp1']);
      expect(changes.deletedExpenses, ['de1']);
      expect(changes.now, '2026-06-05T12:00:00Z');
    });

    test('carries server updated_at onto each entity for LWW', () async {
      final t = _FakeTransport({
        'projects': [
          {..._projectJson(id: 'p1'), 'updated_at': '2026-06-05T11:00:00Z'}
        ],
        'expenses': [
          {..._expenseJson(id: 'e1'), 'updated_at': '2026-06-05T11:30:00Z'}
        ],
        'deleted_projects': [],
        'deleted_expenses': [],
        'now': '2026-06-05T12:00:00Z',
      });
      final changes = await BudgetsRepository(t).fetchChanges();
      expect(changes.projects.first.updatedAt, '2026-06-05T11:00:00Z');
      expect(changes.expenses.first.updatedAt, '2026-06-05T11:30:00Z');
    });

    test('passes since as query param when provided', () async {
      final t = _FakeTransport({
        'projects': [],
        'expenses': [],
        'deleted_projects': [],
        'deleted_expenses': [],
        'now': '2026-06-05T12:00:00Z',
      });
      await BudgetsRepository(t).fetchChanges(since: '2026-06-05T09:00:00Z');
      expect(t.lastQueryParams, containsPair('since', '2026-06-05T09:00:00Z'));
    });

    test('sends no query param on a full (since-null) sync', () async {
      final t = _FakeTransport({
        'projects': [],
        'expenses': [],
        'deleted_projects': [],
        'deleted_expenses': [],
        'now': '2026-06-05T12:00:00Z',
      });
      await BudgetsRepository(t).fetchChanges();
      expect(t.lastQueryParams, isNull);
    });
  });

  group('BudgetsRepository.listBudgetEntries', () {
    test('GET /api/budgets/projects/{id}/budget-entries and parses entries',
        () async {
      final t = _FakeTransport({
        'entries': [
          _entryJson(id: 'be1', amount: 200.0, source: 'client deposit'),
          _entryJson(id: 'be2', amount: -50.0, kind: 'edit'),
        ],
        'count': 2,
      });
      final entries = await BudgetsRepository(t).listBudgetEntries('proj1');
      expect(t.lastMethod, 'GET');
      expect(t.lastPath, '/api/budgets/projects/proj1/budget-entries');
      expect(entries, hasLength(2));
      expect(entries[0], isA<BudgetEntry>());
      expect(entries[0].id, 'be1');
      expect(entries[0].amount, 200.0);
      expect(entries[0].source, 'client deposit');
      expect(entries[1].kind, 'edit');
      expect(entries[1].isEdit, isTrue);
      expect(entries[1].amount, -50.0);
    });

    test('returns empty list when entries key is missing', () async {
      final t = _FakeTransport({});
      final entries = await BudgetsRepository(t).listBudgetEntries('proj1');
      expect(entries, isEmpty);
    });
  });

  group('BudgetsRepository.addBudgetEntry', () {
    test('POST /api/budgets/projects/{id}/budget-entries with amount', () async {
      final t = _FakeTransport({'entry': _entryJson(id: 'new1', amount: 300.0)});
      final entry = await BudgetsRepository(t).addBudgetEntry('proj1', 300.0);
      expect(t.lastMethod, 'POST');
      expect(t.lastPath, '/api/budgets/projects/proj1/budget-entries');
      expect(t.lastBody, containsPair('amount', 300.0));
      expect(entry, isA<BudgetEntry>());
      expect(entry.id, 'new1');
    });

    test('includes source + currency when provided', () async {
      final t = _FakeTransport({'entry': _entryJson()});
      await BudgetsRepository(t)
          .addBudgetEntry('proj1', 100.0, source: 'grant', currency: 'GBP');
      expect(t.lastBody, containsPair('source', 'grant'));
      expect(t.lastBody, containsPair('currency', 'GBP'));
    });

    test('omits source + currency when null', () async {
      final t = _FakeTransport({'entry': _entryJson()});
      await BudgetsRepository(t).addBudgetEntry('proj1', 100.0);
      expect(t.lastBody?.containsKey('source'), isFalse);
      expect(t.lastBody?.containsKey('currency'), isFalse);
    });
  });

  group('BudgetsRepository.updateBudgetEntry', () {
    test('PATCH /api/budgets/entries/{id} with the supplied fields', () async {
      final t = _FakeTransport({'entry': _entryJson(id: 'be9', amount: 150.0)});
      await BudgetsRepository(t)
          .updateBudgetEntry('be9', amount: 150.0, source: 'revised');
      expect(t.lastMethod, 'PATCH');
      expect(t.lastPath, '/api/budgets/entries/be9');
      expect(t.lastBody, containsPair('amount', 150.0));
      expect(t.lastBody, containsPair('source', 'revised'));
    });

    test('omits unspecified fields from the body', () async {
      final t = _FakeTransport({'entry': _entryJson()});
      await BudgetsRepository(t).updateBudgetEntry('be9', amount: 10.0);
      expect(t.lastBody?.containsKey('amount'), isTrue);
      expect(t.lastBody?.containsKey('source'), isFalse);
      expect(t.lastBody?.containsKey('currency'), isFalse);
    });

    test('sends an empty-string source through (clears it server-side)',
        () async {
      final t = _FakeTransport({'entry': _entryJson()});
      await BudgetsRepository(t).updateBudgetEntry('be9', source: '');
      expect(t.lastBody, containsPair('source', ''));
    });
  });

  group('BudgetsRepository.deleteBudgetEntry', () {
    test('DELETE /api/budgets/entries/{id}', () async {
      final t = _FakeTransport({'status': 'deleted'});
      await BudgetsRepository(t).deleteBudgetEntry('be7');
      expect(t.lastMethod, 'DELETE');
      expect(t.lastPath, '/api/budgets/entries/be7');
    });
  });
}
