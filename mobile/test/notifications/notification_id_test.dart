import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/notifications/local_notifications.dart';

void main() {
  group('nextNotificationId', () {
    test('rapid successive calls are all distinct, positive, and 31-bit', () {
      // Regression for the "2 notifications, only one shows" bug: the old id
      // was epoch-millis, so two notifications fired in the same millisecond
      // collided and the second replaced the first. These calls happen back to
      // back (well within one second) with no sleeping — distinctness must come
      // from the per-process counter, not the clock.
      const count = 1000; // > 100, and < the 1024/sec counter headroom
      final ids = <int>{};
      for (var i = 0; i < count; i++) {
        final id = nextNotificationId();
        expect(id, greaterThan(0), reason: 'id must be strictly positive');
        expect(
          id,
          lessThanOrEqualTo(0x7FFFFFFF),
          reason: 'id must fit a positive 31-bit int',
        );
        expect(
          ids.add(id),
          isTrue,
          reason: 'id $id collided at iteration $i (must never repeat)',
        );
      }
      expect(ids.length, count, reason: 'every id was unique');
    });
  });
}
