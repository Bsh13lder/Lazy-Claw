// Pure-predicate tests for the "Add to budget" offline-fallback DECISION.
//
// BUG B5: a transient server 5xx used to surface as a hard snackbar and DROP the
// top-up (budget entries have no outbox → nothing retries → money lost). The
// fallback decision now lives in the pure, widget-free
// `shouldFallbackOfflineForBudget` so it can be unit-tested WITHOUT pumping a
// widget (this sheet's widget tests hang). It must fall back offline for network
// errors (status 0), retryable server errors (status >= 500), and unknown /
// unexpected error shapes — and ONLY surface a hard error for a DEFINITIVE
// client 4xx rejection (400/401/403/404/409/422), where the request was rejected
// (not processed) so there is nothing to retry and no double-credit risk.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';
import 'package:lazyclaw_mobile/screens/expenses/budget_log_sheet.dart';

void main() {
  group('shouldFallbackOfflineForBudget', () {
    test('network error (ApiError status 0) → fall back offline', () {
      expect(shouldFallbackOfflineForBudget(ApiError(0, 'no connection')), isTrue);
    });

    test('retryable server errors (>= 500) → fall back offline (THE bug)', () {
      expect(shouldFallbackOfflineForBudget(ApiError(500, 'boom')), isTrue);
      expect(shouldFallbackOfflineForBudget(ApiError(502, 'bad gateway')), isTrue);
      expect(shouldFallbackOfflineForBudget(ApiError(503, 'unavailable')), isTrue);
    });

    test('unknown / unexpected non-ApiError shape → fall back offline', () {
      expect(shouldFallbackOfflineForBudget(Exception('boom')), isTrue);
      expect(shouldFallbackOfflineForBudget('weird string error'), isTrue);
    });

    test('definitive client 4xx rejections → surface hard error (no fallback)', () {
      expect(shouldFallbackOfflineForBudget(ApiError(400, 'bad request')), isFalse);
      expect(shouldFallbackOfflineForBudget(ApiError(401, 'unauthorized')), isFalse);
      expect(shouldFallbackOfflineForBudget(ApiError(403, 'forbidden')), isFalse);
      expect(shouldFallbackOfflineForBudget(ApiError(404, 'not found')), isFalse);
      expect(shouldFallbackOfflineForBudget(ApiError(409, 'conflict')), isFalse);
      expect(shouldFallbackOfflineForBudget(ApiError(422, 'unprocessable')), isFalse);
    });
  });
}
