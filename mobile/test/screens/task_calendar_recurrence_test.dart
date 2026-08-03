// Unit tests for `expandRecurringForRange` — the pure projector that turns a
// task's `recurring` cron into GHOST calendar entries across a visible
// range, closing the "recurring tasks don't show in the calendar" report
// (the server materialises only ONE occurrence at a time; nothing else
// expands `recurring` on the client — see the 2026-08-03 diagnosis).
//
// Every test passes an explicit `now:` so behavior is pinned to the test's
// own fixture dates, never the real wall clock (the function itself
// defaults `now` to `DateTime.now()` for its real caller, TaskCalendarView).
//
// Framework-light: no widget pump, just the pure day-stepping function.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_calendar_utils.dart';

Task _task(
  String id, {
  String? dueDate,
  String? recurring,
  String status = 'todo',
}) =>
    Task(
      id: id,
      userId: 'u1',
      title: 'Task $id',
      priority: 'medium',
      status: status,
      owner: 'user',
      dueDate: dueDate,
      recurring: recurring,
      nagCount: 0,
      createdAt: '2026-06-06T00:00:00Z',
    );

void main() {
  group('expandRecurringForRange — daily', () {
    test('"0 8 * * *" across a 7-day range yields 7 ghost days', () {
      final task = _task('a', recurring: '0 8 * * *');
      final start = DateTime(2026, 8, 1);
      final end = DateTime(2026, 8, 7);

      final ghosts = expandRecurringForRange([task], start, end, now: start);

      expect(ghosts.keys.length, 7);
      for (var d = start; !d.isAfter(end); d = d.add(const Duration(days: 1))) {
        expect(ghosts[d]!.map((t) => t.id), ['a']);
      }
    });

    test('caps at 60 ghosts per task even across a much wider range', () {
      final task = _task('a', recurring: '0 8 * * *');
      final start = DateTime(2026, 1, 1);
      final end = DateTime(2026, 12, 31); // 365 days

      final ghosts = expandRecurringForRange([task], start, end, now: start);

      final totalGhosts =
          ghosts.values.fold<int>(0, (sum, list) => sum + list.length);
      expect(totalGhosts, 60);
    });
  });

  group('expandRecurringForRange — weekly', () {
    test('"0 9 * * 1" (Monday) yields only Mondays in the range', () {
      final task = _task('a', recurring: '0 9 * * 1');
      // 2026-08-03 is a Monday (pinned — see diagnosis doc dates).
      final start = DateTime(2026, 8, 1); // Saturday
      final end = DateTime(2026, 8, 14); // two weeks later, Friday

      final ghosts = expandRecurringForRange([task], start, end, now: start);

      expect(ghosts.keys.toSet(), {DateTime(2026, 8, 3), DateTime(2026, 8, 10)});
      for (final day in ghosts.keys) {
        expect(day.weekday, DateTime.monday);
      }
    });

    test('"0 9 * * 1-5" (weekdays) skips Saturday and Sunday', () {
      final task = _task('a', recurring: '0 9 * * 1-5');
      final start = DateTime(2026, 8, 3); // Monday
      final end = DateTime(2026, 8, 9); // Sunday

      final ghosts = expandRecurringForRange([task], start, end, now: start);

      expect(ghosts.keys.length, 5);
      for (final day in ghosts.keys) {
        expect(day.weekday, lessThanOrEqualTo(DateTime.friday));
      }
    });
  });

  group('expandRecurringForRange — monthly / yearly', () {
    test('"0 9 15 * *" (15th of each month) hits every 15th in range', () {
      final task = _task('a', recurring: '0 9 15 * *');
      final start = DateTime(2026, 6, 1);
      final end = DateTime(2026, 8, 31);

      final ghosts = expandRecurringForRange([task], start, end, now: start);

      expect(ghosts.keys.toSet(), {
        DateTime(2026, 6, 15),
        DateTime(2026, 7, 15),
        DateTime(2026, 8, 15),
      });
    });

    test('"0 9 25 12 *" (Dec 25 yearly) only matches Dec 25', () {
      final task = _task('a', recurring: '0 9 25 12 *');
      final start = DateTime(2026, 12, 1);
      final end = DateTime(2027, 1, 15);

      final ghosts = expandRecurringForRange([task], start, end, now: start);

      expect(ghosts.keys.toList(), [DateTime(2026, 12, 25)]);
    });
  });

  group('expandRecurringForRange — real-vs-ghost dedup', () {
    test('the real materialised due day is not duplicated as a ghost', () {
      // 2026-08-03 is a Monday; the task's own dueDate lands on one of the
      // cron's own match days — that specific day must NOT get a ghost too
      // (the real TaskRow already covers it via groupTasksByDay).
      final task = _task('a', dueDate: '2026-08-03', recurring: '0 9 * * 1');
      final start = DateTime(2026, 8, 1);
      final end = DateTime(2026, 8, 17);

      final ghosts = expandRecurringForRange([task], start, end, now: start);

      expect(ghosts.containsKey(DateTime(2026, 8, 3)), isFalse);
      expect(ghosts.keys.toSet(), {DateTime(2026, 8, 10), DateTime(2026, 8, 17)});
    });
  });

  group('expandRecurringForRange — unsupported / absent shapes', () {
    test('no `recurring` cron yields no ghosts', () {
      final task = _task('a', dueDate: '2026-08-03');
      final ghosts = expandRecurringForRange(
        [task],
        DateTime(2026, 8, 1),
        DateTime(2026, 8, 31),
        now: DateTime(2026, 8, 1),
      );
      expect(ghosts, isEmpty);
    });

    test('an empty-string `recurring` yields no ghosts', () {
      final task = _task('a', recurring: '');
      final ghosts = expandRecurringForRange(
        [task],
        DateTime(2026, 8, 1),
        DateTime(2026, 8, 31),
        now: DateTime(2026, 8, 1),
      );
      expect(ghosts, isEmpty);
    });

    test('an unparseable cron yields no ghosts (no crash)', () {
      final task = _task('a', recurring: 'not-a-cron');
      final ghosts = expandRecurringForRange(
        [task],
        DateTime(2026, 8, 1),
        DateTime(2026, 8, 31),
        now: DateTime(2026, 8, 1),
      );
      expect(ghosts, isEmpty);
    });

    test('an unsupported shape (step values) yields no ghosts, not a crash', () {
      // */15 minute-step is a real, common cron shape recurrenceFromCron
      // does not classify (not authored by the picker) → RecurrenceKind
      // .custom → no ghosts, per "do not write a general cron engine".
      final task = _task('a', recurring: '*/15 * * * *');
      final ghosts = expandRecurringForRange(
        [task],
        DateTime(2026, 8, 1),
        DateTime(2026, 8, 31),
        now: DateTime(2026, 8, 1),
      );
      expect(ghosts, isEmpty);
    });
  });

  group('expandRecurringForRange — range edges', () {
    test('an inverted range (end before start) yields an empty map', () {
      final task = _task('a', recurring: '0 8 * * *');
      final ghosts = expandRecurringForRange(
        [task],
        DateTime(2026, 8, 10),
        DateTime(2026, 8, 1),
        now: DateTime(2026, 8, 1),
      );
      expect(ghosts, isEmpty);
    });

    test('multiple recurring tasks each contribute their own ghosts', () {
      final daily = _task('daily', recurring: '0 8 * * *');
      final monday = _task('mon', recurring: '0 9 * * 1');
      final start = DateTime(2026, 8, 3); // Monday
      final end = DateTime(2026, 8, 4); // Tuesday

      final ghosts = expandRecurringForRange(
        [daily, monday],
        start,
        end,
        now: start,
      );

      expect(ghosts[DateTime(2026, 8, 3)]!.map((t) => t.id).toSet(),
          {'daily', 'mon'});
      expect(ghosts[DateTime(2026, 8, 4)]!.map((t) => t.id), ['daily']);
    });
  });

  group('expandRecurringForRange — never project into the past', () {
    // Ghosts are a forward-looking "here's the next repeat" hint. Paging the
    // calendar back to a month before the task existed must NOT paint ghost
    // dots on every matching day in that month — that reads as "phantom
    // repeats in my history" that never happened.
    test('a range entirely before today yields no ghosts at all', () {
      final task = _task('a', recurring: '0 8 * * *');
      final ghosts = expandRecurringForRange(
        [task],
        DateTime(2026, 6, 1),
        DateTime(2026, 6, 30),
        now: DateTime(2026, 8, 15), // "today" is well after this range
      );
      expect(ghosts, isEmpty);
    });

    test(
      'a range spanning past-into-future is clamped to start at today, not '
      'at rangeStart',
      () {
        final task = _task('a', recurring: '0 8 * * *');
        final now = DateTime(2026, 8, 15);
        final ghosts = expandRecurringForRange(
          [task],
          DateTime(2026, 7, 1), // well before "today"
          DateTime(2026, 8, 31),
          now: now,
        );

        // Every ghost day is today or later — nothing in July, nothing
        // before Aug 15.
        for (final day in ghosts.keys) {
          expect(day.isBefore(now), isFalse);
        }
        expect(ghosts.containsKey(DateTime(2026, 8, 15)), isTrue);
        expect(ghosts.containsKey(DateTime(2026, 7, 15)), isFalse);
        expect(ghosts.containsKey(DateTime(2026, 8, 1)), isFalse);
        // Aug 15 through Aug 31 inclusive = 17 daily ghost days.
        expect(ghosts.keys.length, 17);
      },
    );

    test(
      'a ghost exactly on today is allowed (today is not "the past")',
      () {
        // 2026-08-03 is a Monday; cron matches Mondays; "now" IS that day.
        final task = _task('a', recurring: '0 9 * * 1');
        final now = DateTime(2026, 8, 3);
        final ghosts = expandRecurringForRange(
          [task],
          DateTime(2026, 7, 1),
          DateTime(2026, 8, 31),
          now: now,
        );

        expect(ghosts.containsKey(DateTime(2026, 8, 3)), isTrue);
      },
    );

    test(
      'the past-clamp and the real-vs-ghost dedup compose correctly: a real '
      'occurrence today still suppresses its own ghost',
      () {
        final task = _task('a', dueDate: '2026-08-03', recurring: '0 9 * * 1');
        final now = DateTime(2026, 8, 3);
        final ghosts = expandRecurringForRange(
          [task],
          DateTime(2026, 7, 1),
          DateTime(2026, 8, 31),
          now: now,
        );

        expect(ghosts.containsKey(DateTime(2026, 8, 3)), isFalse);
        expect(ghosts.keys.toSet(), {DateTime(2026, 8, 10), DateTime(2026, 8, 17), DateTime(2026, 8, 24), DateTime(2026, 8, 31)});
      },
    );
  });
}
