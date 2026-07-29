// The health-critical path: the ON-DEVICE alarm must fire at the user's local
// wall-clock. `reminderFireTime` feeds the scheduler, which builds a
// `tz.TZDateTime` from the returned DateTime's LOCAL components. So a reminder
// stored UTC-aware (noon Madrid = 10:00Z) must come back as local noon, not the
// raw 10:00 UTC hour — otherwise a medication reminder fires 2h early.
//
// tz-agnostic: inputs are built by round-tripping a known LOCAL instant through
// `.toUtc()`, so the assertions hold whether the test host is Madrid or UTC.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/notifications/task_reminder_service.dart';
import 'package:lazyclaw_mobile/models/task.dart';

String _twoDaysOutAt(int hour, int minute) {
  final f = DateTime.now().add(const Duration(days: 2));
  return DateTime(f.year, f.month, f.day, hour, minute).toUtc().toIso8601String();
}

String _twoDaysOutNaive(int hour, int minute) {
  final f = DateTime.now().add(const Duration(days: 2));
  String p(int n) => n.toString().padLeft(2, '0');
  return '${f.year.toString().padLeft(4, '0')}-${p(f.month)}-${p(f.day)}'
      'T${p(hour)}:${p(minute)}:00';
}

void main() {
  group('reminderFireTime timezone handling', () {
    test('UTC-aware reminderAt fires at the local wall-clock', () {
      final task = Task.fromJson({'reminder_at': _twoDaysOutAt(12, 0)});
      final fire = reminderFireTime(task);
      expect(fire, isNotNull);
      // Scheduler reads these LOCAL components → must be noon, not 10 (UTC).
      expect(fire!.hour, 12);
      expect(fire.minute, 0);
    });

    test('naive reminderAt is unchanged (already local wall-clock)', () {
      final task = Task.fromJson({'reminder_at': _twoDaysOutNaive(8, 15)});
      final fire = reminderFireTime(task);
      expect(fire, isNotNull);
      expect(fire!.hour, 8);
      expect(fire.minute, 15);
    });

    test('UTC-aware timed dueDate also fires at local wall-clock', () {
      final task = Task.fromJson({'due_date': _twoDaysOutAt(9, 30)});
      final fire = reminderFireTime(task);
      expect(fire, isNotNull);
      expect(fire!.hour, 9);
      expect(fire.minute, 30);
    });
  });
}
