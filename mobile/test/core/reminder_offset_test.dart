// Unit tests for the pure reminder-OFFSET model + math in
// core/reminder_offset.dart. Plain Dart (no plugin, no binding).
//
// An "offset" is the wire form the server persists in
// `users.settings.general.reminder_offsets`: a signed `<n><unit>` string
// (`0m`, `-10m`, `-30m`, `-1h`, `-1d`, `-2h30m`) where a NEGATIVE value means
// "fire that long BEFORE the due time". `fire = base + offsetDuration`.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/reminder_offset.dart';

void main() {
  group('parseReminderOffset', () {
    test('"0m" → zero (at time)', () {
      expect(parseReminderOffset('0m'), Duration.zero);
    });

    test('"-0m" → zero', () {
      expect(parseReminderOffset('-0m'), Duration.zero);
    });

    test('"-10m" → −10 minutes', () {
      expect(parseReminderOffset('-10m'), const Duration(minutes: -10));
    });

    test('"-30m" → −30 minutes', () {
      expect(parseReminderOffset('-30m'), const Duration(minutes: -30));
    });

    test('"-1h" → −1 hour', () {
      expect(parseReminderOffset('-1h'), const Duration(hours: -1));
    });

    test('"-1d" → −1 day', () {
      expect(parseReminderOffset('-1d'), const Duration(days: -1));
    });

    test('"-2h30m" → −150 minutes (compound)', () {
      expect(parseReminderOffset('-2h30m'), const Duration(minutes: -150));
    });

    test('"-1d2h" → −(1440+120) minutes (compound day+hour)', () {
      expect(parseReminderOffset('-1d2h'), const Duration(minutes: -1560));
    });

    test('surrounding whitespace is tolerated', () {
      expect(parseReminderOffset('  -30m '), const Duration(minutes: -30));
    });

    test('a positive (no-sign) magnitude parses as positive', () {
      // We normalise to negative for storage, but the parser is sign-faithful.
      expect(parseReminderOffset('30m'), const Duration(minutes: 30));
    });

    test('invalid strings → null', () {
      for (final bad in ['', '   ', '-', '30', '30x', '1h30', '1.5h', 'abc',
        'm', '-m', '--30m', '30m-']) {
        expect(parseReminderOffset(bad), isNull, reason: 'expected null for "$bad"');
      }
    });
  });

  group('serializeReminderOffset', () {
    test('zero → "0m"', () {
      expect(serializeReminderOffset(Duration.zero), '0m');
    });

    test('−30 min → "-30m"', () {
      expect(serializeReminderOffset(const Duration(minutes: -30)), '-30m');
    });

    test('−1 hour → "-1h"', () {
      expect(serializeReminderOffset(const Duration(hours: -1)), '-1h');
    });

    test('−1 day → "-1d"', () {
      expect(serializeReminderOffset(const Duration(days: -1)), '-1d');
    });

    test('−150 min → "-2h30m" (compound canonical)', () {
      expect(serializeReminderOffset(const Duration(minutes: -150)), '-2h30m');
    });

    test('−1560 min → "-1d2h"', () {
      expect(serializeReminderOffset(const Duration(minutes: -1560)), '-1d2h');
    });
  });

  group('round-trip parse ↔ serialize', () {
    for (final s in const ['0m', '-10m', '-30m', '-1h', '-1d', '-2h30m', '-1d2h']) {
      test('round-trips "$s"', () {
        final d = parseReminderOffset(s);
        expect(d, isNotNull);
        expect(serializeReminderOffset(d!), s);
      });
    }
  });

  group('canonicalReminderOffset', () {
    test('canonicalises "-0m" → "0m"', () {
      expect(canonicalReminderOffset('-0m'), '0m');
    });

    test('canonicalises "-60m" → "-1h"', () {
      expect(canonicalReminderOffset('-60m'), '-1h');
    });

    test('invalid → null', () {
      expect(canonicalReminderOffset('nope'), isNull);
    });
  });

  group('normalizeReminderOffsets', () {
    test('drops invalid, canonicalises, dedupes, sorts earliest-fire-first', () {
      final out = normalizeReminderOffsets(
        ['-30m', 'garbage', '-60m', '-1h', '0m', '-30m'],
      );
      // -1h and -60m collapse; -30m dedupes; sorted most-negative first.
      expect(out, ['-1h', '-30m', '0m']);
    });

    test('empty input → empty list', () {
      expect(normalizeReminderOffsets(const []), isEmpty);
    });

    test('all-invalid input → empty list', () {
      expect(normalizeReminderOffsets(const ['x', '', '-']), isEmpty);
    });
  });

  group('kReminderOffsetOptions', () {
    test('exposes the five UI options in display order with valid wire values',
        () {
      expect(kReminderOffsetOptions.map((o) => o.value).toList(),
          ['0m', '-10m', '-30m', '-1h', '-1d']);
      for (final o in kReminderOffsetOptions) {
        expect(parseReminderOffset(o.value), isNotNull,
            reason: '${o.value} must parse');
        expect(o.label, isNotEmpty);
      }
    });

    test('default matches the legacy single-select default (30 min before)', () {
      expect(kDefaultReminderOffsets, ['-30m']);
    });
  });

  group('computeOffsetFireTimes', () {
    final now = DateTime(2026, 6, 6, 12, 0, 0);
    final base = DateTime(2026, 6, 7, 17, 0, 0); // future due

    test('multiple offsets → multiple sorted absolute instants', () {
      final fires = computeOffsetFireTimes(
        base: base,
        offsets: const ['0m', '-30m', '-1d'],
        now: now,
      );
      expect(fires, [
        DateTime(2026, 6, 6, 17, 0, 0), // -1d
        DateTime(2026, 6, 7, 16, 30, 0), // -30m
        DateTime(2026, 6, 7, 17, 0, 0), // 0m
      ]);
    });

    test('past offsets (more than grace in the past) are skipped', () {
      // now is Jun 7 12:00; base Jun 7 17:00; -1d fire = Jun 6 17:00 (past).
      final fires = computeOffsetFireTimes(
        base: base,
        offsets: const ['0m', '-1d'],
        now: DateTime(2026, 6, 7, 12, 0, 0),
      );
      expect(fires, [DateTime(2026, 6, 7, 17, 0, 0)]); // only 0m survives
    });

    test('a fire within the grace window is kept', () {
      final b = DateTime(2026, 6, 7, 12, 0, 0);
      final fires = computeOffsetFireTimes(
        base: b,
        offsets: const ['0m'],
        now: DateTime(2026, 6, 7, 12, 0, 30), // 30s after base
        grace: const Duration(minutes: 1),
      );
      expect(fires, [b]);
    });

    test('duplicate offsets collapse to one instant', () {
      final fires = computeOffsetFireTimes(
        base: base,
        offsets: const ['-30m', '-30m'],
        now: now,
      );
      expect(fires, [DateTime(2026, 6, 7, 16, 30, 0)]);
    });

    test('invalid offsets are ignored', () {
      final fires = computeOffsetFireTimes(
        base: base,
        offsets: const ['garbage', '0m'],
        now: now,
      );
      expect(fires, [DateTime(2026, 6, 7, 17, 0, 0)]);
    });

    test('empty offsets → the base instant alone (single/base)', () {
      final fires = computeOffsetFireTimes(
        base: base,
        offsets: const [],
        now: now,
      );
      expect(fires, [base]);
    });

    test('empty offsets with a past base → nothing', () {
      final fires = computeOffsetFireTimes(
        base: DateTime(2026, 6, 5, 17, 0, 0),
        offsets: const [],
        now: now,
      );
      expect(fires, isEmpty);
    });
  });
}
