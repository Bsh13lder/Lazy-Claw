import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/notifications/local_notifications.dart';
import 'package:timezone/timezone.dart' as tz;

/// The instant a wall-clock time in [loc] actually represents, as epoch ms —
/// what `zonedSchedule` ultimately arms the alarm at.
int _epochOf(tz.Location loc, int y, int m, int d, int h, [int min = 0]) =>
    tz.TZDateTime(loc, y, m, d, h, min).millisecondsSinceEpoch;

void main() {
  group('fixedOffsetLocation', () {
    test('+02:00 (Madrid summer): 18:00 local is the 16:00 UTC instant', () {
      final loc = fixedOffsetLocation(const Duration(hours: 2));
      expect(
        _epochOf(loc, 2026, 7, 30, 18),
        DateTime.utc(2026, 7, 30, 16).millisecondsSinceEpoch,
      );
    });

    test('the UTC fallback bug shape: a UTC location would be 2h late', () {
      // Regression guard for the original bug: with tz.local == UTC, the same
      // 18:00 wall-clock components produced the 18:00 UTC instant (= 20:00
      // Madrid). The fixed-offset location must NOT equal that.
      final fixed = fixedOffsetLocation(const Duration(hours: 2));
      expect(
        _epochOf(fixed, 2026, 7, 30, 18),
        isNot(DateTime.utc(2026, 7, 30, 18).millisecondsSinceEpoch),
      );
    });

    test('negative offset -05:00: 18:00 local is the 23:00 UTC instant', () {
      final loc = fixedOffsetLocation(const Duration(hours: -5));
      expect(
        _epochOf(loc, 2026, 7, 30, 18),
        DateTime.utc(2026, 7, 30, 23).millisecondsSinceEpoch,
      );
    });

    test('half-hour offset +05:30: 18:00 local is the 12:30 UTC instant', () {
      final loc = fixedOffsetLocation(const Duration(hours: 5, minutes: 30));
      expect(
        _epochOf(loc, 2026, 7, 30, 18),
        DateTime.utc(2026, 7, 30, 12, 30).millisecondsSinceEpoch,
      );
    });

    test('zero offset behaves as UTC', () {
      final loc = fixedOffsetLocation(Duration.zero);
      expect(
        _epochOf(loc, 2026, 7, 30, 18),
        DateTime.utc(2026, 7, 30, 18).millisecondsSinceEpoch,
      );
    });

    test('round-trips a UTC instant to the shifted wall-clock', () {
      final loc = fixedOffsetLocation(const Duration(hours: 2));
      final local = tz.TZDateTime.from(DateTime.utc(2026, 1, 1, 10), loc);
      expect(local.hour, 12);
      expect(local.minute, 0);
    });

    test('names the location by its offset and marks it non-DST', () {
      expect(fixedOffsetLocation(const Duration(hours: 2)).name, 'UTC+02:00');
      expect(fixedOffsetLocation(const Duration(hours: -5)).name, 'UTC-05:00');
      expect(
        fixedOffsetLocation(const Duration(hours: 5, minutes: 30)).name,
        'UTC+05:30',
      );
      expect(fixedOffsetLocation(Duration.zero).name, 'UTC+00:00');

      final zone = fixedOffsetLocation(const Duration(hours: 2)).zones.single;
      expect(zone.isDst, isFalse);
      expect(zone.offset, const Duration(hours: 2).inMilliseconds);
      expect(zone.abbreviation, 'UTC+02:00');
    });

    test('covers instants far in the past and future (single transition)', () {
      final loc = fixedOffsetLocation(const Duration(hours: 2));
      expect(
        _epochOf(loc, 1971, 1, 1, 12),
        DateTime.utc(1971, 1, 1, 10).millisecondsSinceEpoch,
      );
      expect(
        _epochOf(loc, 2099, 12, 31, 12),
        DateTime.utc(2099, 12, 31, 10).millisecondsSinceEpoch,
      );
    });
  });
}
