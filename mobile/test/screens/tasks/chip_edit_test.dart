// Unit tests for the pure chip-edit helpers (due-date recomposition + priority
// label/color). The picker functions are exercised by the widget tests.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/due_date.dart';
import 'package:lazyclaw_mobile/screens/tasks/chip_edit.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

void main() {
  group('withNewDueDay', () {
    test('preserves an existing time-of-day onto the new day', () {
      final result = withNewDueDay('2026-06-10T17:30:00', DateTime(2026, 7, 1));
      expect(result, '2026-07-01T17:30:00');
      expect(dueDateHasTime(result), isTrue);
    });

    test('keeps a date-only due as date-only', () {
      final result = withNewDueDay('2026-06-10', DateTime(2026, 7, 1));
      expect(result, '2026-07-01');
      expect(dueDateHasTime(result), isFalse);
    });

    test('a null old due yields a bare date-only string', () {
      final result = withNewDueDay(null, DateTime(2026, 7, 1));
      expect(result, '2026-07-01');
    });
  });

  group('withNewDueTime', () {
    test('preserves the calendar day and sets the new time', () {
      final result = withNewDueTime('2026-06-10', 8, 5);
      expect(result, '2026-06-10T08:05:00');
    });

    test('replaces an existing time while keeping the day', () {
      final result = withNewDueTime('2026-06-10T17:30:00', 9, 0);
      expect(result, '2026-06-10T09:00:00');
    });
  });

  group('priorityLabel', () {
    test('capitalizes the first letter', () {
      expect(priorityLabel('high'), 'High');
      expect(priorityLabel('urgent'), 'Urgent');
    });

    test('tolerates an empty string', () {
      expect(priorityLabel(''), '');
    });
  });

  group('priorityColor', () {
    test('maps each level to its kit accent', () {
      expect(priorityColor('urgent'), AppColors.error);
      expect(priorityColor('high'), AppColors.warn);
      expect(priorityColor('medium'), AppColors.info);
      expect(priorityColor('low'), AppColors.textMuted);
      expect(priorityColor('unknown'), AppColors.textMuted);
    });
  });

  test('kTaskPriorities is the canonical ordered set', () {
    expect(kTaskPriorities, ['low', 'medium', 'high', 'urgent']);
  });
}
