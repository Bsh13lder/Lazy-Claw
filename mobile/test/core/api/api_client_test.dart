import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/api/api_exceptions.dart';

void main() {
  test('ApiError classifies status codes', () {
    expect(ApiError(401, 'x').isUnauthorized, isTrue);
    expect(ApiError(404, 'x').isNotFound, isTrue);
    expect(ApiError(403, 'x').isForbidden, isTrue);
    expect(ApiError(500, 'x').isUnauthorized, isFalse);
  });
}
