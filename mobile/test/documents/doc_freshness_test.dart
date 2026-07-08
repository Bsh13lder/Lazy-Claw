import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/documents/doc_freshness.dart';

void main() {
  group('isServerNewer', () {
    test('true when the server stamp is strictly newer', () {
      expect(
        isServerNewer(
          '2026-07-01T10:00:00.000000+00:00',
          '2026-07-01T10:05:00.000000+00:00',
        ),
        isTrue,
      );
    });

    test('false when equal (no reload thrash on an unchanged doc)', () {
      const t = '2026-07-01T10:00:00.000000+00:00';
      expect(isServerNewer(t, t), isFalse);
    });

    test('false when the cached copy is newer (local edit not yet synced)', () {
      expect(
        isServerNewer(
          '2026-07-01T10:05:00.000000+00:00',
          '2026-07-01T10:00:00.000000+00:00',
        ),
        isFalse,
      );
    });

    test('unknowns are never "newer" (prefer cache offline / pre-load)', () {
      expect(isServerNewer(null, '2026-07-01T10:00:00.000000+00:00'), isFalse);
      expect(isServerNewer('2026-07-01T10:00:00.000000+00:00', null), isFalse);
      expect(isServerNewer(null, null), isFalse);
    });

    test('microsecond precision is honoured (same second, later micros)', () {
      expect(
        isServerNewer(
          '2026-07-01T10:00:00.000001+00:00',
          '2026-07-01T10:00:00.000002+00:00',
        ),
        isTrue,
      );
    });
  });
}
