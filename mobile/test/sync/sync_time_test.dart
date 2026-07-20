import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/sync/sync_time.dart';

void main() {
  group('parseInstantMicros', () {
    test('parses Z, +00:00, and space-separated to the SAME instant', () {
      final z = parseInstantMicros('2026-06-05T11:00:00.000Z');
      final off = parseInstantMicros('2026-06-05T11:00:00.000000+00:00');
      final sp = parseInstantMicros('2026-06-05 11:00:00.000000');
      expect(z, isNotNull);
      expect(z, equals(off));
      expect(z, equals(sp));
    });

    test('returns null for empty/garbage', () {
      expect(parseInstantMicros(''), isNull);
      expect(parseInstantMicros(null), isNull);
      expect(parseInstantMicros('not-a-date'), isNull);
    });
  });

  group('serverWinsByTime (replaces lexical _gte)', () {
    test('SAME instant, server=+00:00 vs local=Z → server wins (the live bug)',
        () {
      // Lexical compareTo ranks "...000000+00:00" < "...000Z" (0x30 < 0x5A),
      // so the old _gte returned false and the phone kept the stale local row.
      const server = '2026-06-05T11:00:00.000000+00:00';
      const local = '2026-06-05T11:00:00.000Z';
      expect(serverWinsByTime(server, local), isTrue);
    });

    test('server strictly newer → server wins', () {
      expect(
        serverWinsByTime('2026-06-05T12:00:00Z', '2026-06-05T11:00:00Z'),
        isTrue,
      );
    });

    test('local strictly newer → local wins', () {
      expect(
        serverWinsByTime('2026-06-05T11:00:00Z', '2026-06-05T12:00:00Z'),
        isFalse,
      );
    });

    test('empty server never wins; empty local always loses', () {
      expect(serverWinsByTime('', '2026-06-05T11:00:00Z'), isFalse);
      expect(serverWinsByTime('2026-06-05T11:00:00Z', ''), isTrue);
    });

    test('unparseable either side → lexical fallback (no crash)', () {
      expect(serverWinsByTime('zzz', 'aaa'), isTrue); // 'zzz' >= 'aaa'
    });
  });
}
