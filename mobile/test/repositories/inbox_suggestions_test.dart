import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/repositories/budgets_repository.dart';

// ── Fake transport ─────────────────────────────────────────────────────────

class _FakeBudgetsTransport implements BudgetsTransport {
  String? lastMethod;
  String? lastPath;
  Map<String, dynamic>? lastBody;
  Map<String, dynamic>? lastQueryParams;

  Map<String, dynamic> response;

  _FakeBudgetsTransport(this.response);

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

/// Throws the PRODUCTION exception shape (per sync-engine lesson).
class _ThrowingBudgetsTransport implements BudgetsTransport {
  @override
  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, dynamic>? queryParams,
  }) async {
    throw DioException(
      requestOptions: RequestOptions(path: path),
      error: ApiError(503, 'Service unavailable'),
    );
  }

  @override
  Future<Map<String, dynamic>> postJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    throw DioException(
      requestOptions: RequestOptions(path: path),
      error: ApiError(503, 'Service unavailable'),
    );
  }

  @override
  Future<Map<String, dynamic>> patchJson(
    String path,
    Map<String, dynamic> body,
  ) async {
    throw DioException(
      requestOptions: RequestOptions(path: path),
      error: ApiError(503, 'Service unavailable'),
    );
  }

  @override
  Future<Map<String, dynamic>> deleteJson(String path) async {
    throw DioException(
      requestOptions: RequestOptions(path: path),
      error: ApiError(503, 'Service unavailable'),
    );
  }
}

// ── Fixtures ───────────────────────────────────────────────────────────────

Map<String, dynamic> _suggestionJson({
  String expenseId = 'exp1',
  String? projectId = 'proj1',
  String? projectName = 'Groceries',
  String confidence = 'high',
  String? reason = 'Category match',
}) =>
    {
      'expense_id': expenseId,
      'project_id': projectId,
      'project_name': projectName,
      'confidence': confidence,
      'reason': reason,
    };

// ── Tests ──────────────────────────────────────────────────────────────────

void main() {
  group('BudgetsRepository.getInboxSuggestions', () {
    test('POST /api/budgets/inbox/suggestions and parses response', () async {
      final t = _FakeBudgetsTransport({
        'suggestions': [
          _suggestionJson(
            expenseId: 'exp1',
            projectId: 'proj1',
            projectName: 'Groceries',
            confidence: 'high',
            reason: 'Category match',
          ),
        ],
        'skipped': 2,
      });
      final repo = BudgetsRepository(t);
      final result = await repo.getInboxSuggestions();

      expect(t.lastMethod, 'POST');
      expect(t.lastPath, '/api/budgets/inbox/suggestions');
      expect(t.lastBody, {'expense_ids': null});
      expect(result.suggestions, hasLength(1));
      expect(result.suggestions[0].expenseId, 'exp1');
      expect(result.suggestions[0].projectId, 'proj1');
      expect(result.suggestions[0].projectName, 'Groceries');
      expect(result.suggestions[0].confidence, 'high');
      expect(result.suggestions[0].reason, 'Category match');
      expect(result.skipped, 2);
    });

    test('sends expenseIds as array when provided', () async {
      final t = _FakeBudgetsTransport({
        'suggestions': [],
        'skipped': 0,
      });
      await BudgetsRepository(t)
          .getInboxSuggestions(expenseIds: ['exp1', 'exp2']);
      expect(t.lastBody, {'expense_ids': ['exp1', 'exp2']});
    });

    test('sends empty array when expenseIds is empty list', () async {
      final t = _FakeBudgetsTransport({
        'suggestions': [],
        'skipped': 0,
      });
      await BudgetsRepository(t).getInboxSuggestions(expenseIds: []);
      expect(t.lastBody, {'expense_ids': []});
    });

    test('sends null when expenseIds is not provided', () async {
      final t = _FakeBudgetsTransport({
        'suggestions': [],
        'skipped': 0,
      });
      await BudgetsRepository(t).getInboxSuggestions();
      expect(t.lastBody, {'expense_ids': null});
    });

    test('propagates ApiError on transport failure', () async {
      final repo = BudgetsRepository(_ThrowingBudgetsTransport());
      expect(
        () => repo.getInboxSuggestions(),
        throwsA(isA<DioException>()),
      );
    });

    test('handles suggestions with null projectId and projectName', () async {
      final t = _FakeBudgetsTransport({
        'suggestions': [
          _suggestionJson(
            expenseId: 'exp1',
            projectId: null,
            projectName: null,
            confidence: 'low',
            reason: null,
          ),
        ],
        'skipped': 0,
      });
      final repo = BudgetsRepository(t);
      final result = await repo.getInboxSuggestions();

      expect(result.suggestions, hasLength(1));
      expect(result.suggestions[0].projectId, isNull);
      expect(result.suggestions[0].projectName, isNull);
      expect(result.suggestions[0].reason, isNull);
      expect(result.skipped, 0);
    });
  });
}
