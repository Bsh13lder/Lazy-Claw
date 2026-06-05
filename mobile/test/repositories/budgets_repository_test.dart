import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/repositories/budgets_repository.dart';
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
  });
}
