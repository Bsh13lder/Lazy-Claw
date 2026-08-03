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
  String? recurUntil,
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
      recurUntil: recurUntil,
      nagCount: 0,
      createdAt: '2026-06-06T00:00:00Z',
    );

/// The ghost days [ghosts] covers, oldest first — the shape most bound
/// assertions below want (contiguity, first/last day) without each test
/// re-sorting an unordered map's keys.
List<DateTime> _sortedDays(Map<DateTime, List<Task>> ghosts) {
  final days = [...ghosts.keys]..sort();
  return days;
}

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

    test(
      'a year-wide range is bounded by the forward horizon, not by the '
      'caller — and the bound is a clean cut at the horizon day',
      () {
        // Was "caps at 60 ghosts per task": the 60-ghost counter used to be
        // the only bound, so a 365-day request silently stopped mid-range.
        // The horizon owns the bound now — see the "forward horizon" group.
        final task = _task('a', recurring: '0 8 * * *');
        final start = DateTime(2026, 1, 1);
        final end = DateTime(2026, 12, 31); // 365 days

        final ghosts = expandRecurringForRange([task], start, end, now: start);

        final days = _sortedDays(ghosts);
        expect(days.first, DateTime(2026, 1, 1));
        expect(
          days.last,
          DateTime(2026, 1, 1 + kGhostHorizonDays),
          reason: 'the last ghost is the horizon day itself, inclusive',
        );
        expect(days.length, kGhostHorizonDays + 1); // today + horizon days
      },
    );
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

  // ── recurUntil: the user's own explicit series end ──────────────────────
  //
  // The user-reported bug (2026-08-03): a task the user explicitly capped in
  // the detail sheet's ENDS section still ghosted forever, because the
  // expander never read `Task.recurUntil` at all. `recur_until` is date-only
  // `yyyy-MM-dd` meaning "the series runs THROUGH the end of that day", which
  // the backend already honours on respawn (`tasks/store.py:_series_expired`)
  // — the calendar must mirror it or it shows repeats the server will never
  // materialise.
  group('expandRecurringForRange — recurUntil (series end)', () {
    test('a daily series stops on its recurUntil day, with nothing after', () {
      final task =
          _task('a', recurring: '0 8 * * *', recurUntil: '2026-08-10');

      final ghosts = expandRecurringForRange(
        [task],
        DateTime(2026, 8, 1),
        DateTime(2026, 8, 31),
        now: DateTime(2026, 8, 1),
      );

      expect(_sortedDays(ghosts).last, DateTime(2026, 8, 10));
      expect(ghosts.containsKey(DateTime(2026, 8, 10)), isTrue);
      expect(ghosts.containsKey(DateTime(2026, 8, 11)), isFalse);
      expect(ghosts.keys.length, 10); // Aug 1 .. Aug 10 inclusive
    });

    test(
      'recurUntil landing exactly ON a matching day still ghosts that day '
      '(the end is INCLUSIVE, not exclusive)',
      () {
        // 2026-08-03 is a Monday, so Aug 10 is the next Monday — and it is
        // also the series end. An exclusive read would drop the user\'s final
        // occurrence, which is the one they most want to see coming.
        final task =
            _task('a', recurring: '0 9 * * 1', recurUntil: '2026-08-10');

        final ghosts = expandRecurringForRange(
          [task],
          DateTime(2026, 8, 1),
          DateTime(2026, 8, 31),
          now: DateTime(2026, 8, 1),
        );

        expect(ghosts.keys.toSet(),
            {DateTime(2026, 8, 3), DateTime(2026, 8, 10)});
      },
    );

    test('a full-ISO recurUntil is honoured at day granularity', () {
      // The sheet writes date-only, but the server may hand back a datetime
      // (or a tz-aware instant). The calendar's unit is a DAY, so the whole
      // end day stays eligible either way.
      final task = _task(
        'a',
        recurring: '0 8 * * *',
        recurUntil: '2026-08-10T23:59:59',
      );

      final ghosts = expandRecurringForRange(
        [task],
        DateTime(2026, 8, 1),
        DateTime(2026, 8, 31),
        now: DateTime(2026, 8, 1),
      );

      expect(ghosts.containsKey(DateTime(2026, 8, 10)), isTrue);
      expect(ghosts.containsKey(DateTime(2026, 8, 11)), isFalse);
    });

    test('a recurUntil already in the past yields no ghosts at all', () {
      final task =
          _task('a', recurring: '0 8 * * *', recurUntil: '2026-07-30');

      final ghosts = expandRecurringForRange(
        [task],
        DateTime(2026, 8, 1),
        DateTime(2026, 8, 31),
        now: DateTime(2026, 8, 1),
      );

      expect(ghosts, isEmpty);
    });

    test('a null recurUntil means "never ends" — behavior is unchanged', () {
      final task = _task('a', recurring: '0 8 * * *');

      final ghosts = expandRecurringForRange(
        [task],
        DateTime(2026, 8, 1),
        DateTime(2026, 8, 7),
        now: DateTime(2026, 8, 1),
      );

      expect(ghosts.keys.length, 7);
    });

    test('a blank recurUntil means "never ends", never "ends today"', () {
      // '' is the CLEAR sentinel the DAO/provider ride on updates — reading
      // it as a date would silently kill every series the user un-capped.
      final task = _task('a', recurring: '0 8 * * *', recurUntil: '');

      final ghosts = expandRecurringForRange(
        [task],
        DateTime(2026, 8, 1),
        DateTime(2026, 8, 7),
        now: DateTime(2026, 8, 1),
      );

      expect(ghosts.keys.length, 7);
    });

    test('an unparseable recurUntil means "never ends", not "ends now"', () {
      // Fail OPEN, matching `_series_expired`'s `return False` on a bad
      // parse: a garbage end date must not silently erase a live series.
      final task =
          _task('a', recurring: '0 8 * * *', recurUntil: 'not-a-date');

      final ghosts = expandRecurringForRange(
        [task],
        DateTime(2026, 8, 1),
        DateTime(2026, 8, 7),
        now: DateTime(2026, 8, 1),
      );

      expect(ghosts.keys.length, 7);
    });

    test('recurUntil bounds ONLY its own task, never its neighbours', () {
      final capped =
          _task('capped', recurring: '0 8 * * *', recurUntil: '2026-08-03');
      final open = _task('open', recurring: '0 8 * * *');

      final ghosts = expandRecurringForRange(
        [capped, open],
        DateTime(2026, 8, 1),
        DateTime(2026, 8, 7),
        now: DateTime(2026, 8, 1),
      );

      expect(ghosts[DateTime(2026, 8, 3)]!.map((t) => t.id).toSet(),
          {'capped', 'open'});
      expect(ghosts[DateTime(2026, 8, 4)]!.map((t) => t.id), ['open']);
      expect(ghosts[DateTime(2026, 8, 7)]!.map((t) => t.id), ['open']);
    });
  });

  // ── Forward horizon ─────────────────────────────────────────────────────
  //
  // The other half of the 2026-08-03 report: paging to July 2027 painted a
  // repeat dot on EVERY day of that month. A dot a year out carries zero
  // information and reads as data the user never entered.
  group('expandRecurringForRange — forward horizon', () {
    test(
      'the July-2027 regression: a range entirely beyond the horizon yields '
      'ZERO ghosts',
      () {
        final task = _task('a', recurring: '0 8 * * *');

        final ghosts = expandRecurringForRange(
          [task],
          DateTime(2027, 7, 1),
          DateTime(2027, 7, 31),
          now: DateTime(2026, 8, 3),
        );

        expect(ghosts, isEmpty);
      },
    );

    test('a range straddling the horizon is cut exactly at the boundary', () {
      final task = _task('a', recurring: '0 8 * * *');
      final now = DateTime(2026, 8, 3);
      // Pins the constant itself: Aug 3 + 92 days = Nov 3.
      final horizonEnd = DateTime(2026, 8, 3 + kGhostHorizonDays);
      expect(horizonEnd, DateTime(2026, 11, 3));

      final ghosts = expandRecurringForRange(
        [task],
        DateTime(2026, 10, 1),
        DateTime(2026, 12, 31),
        now: now,
      );

      expect(ghosts.containsKey(horizonEnd), isTrue,
          reason: 'the horizon day itself is inclusive');
      expect(ghosts.containsKey(DateTime(2026, 11, 4)), isFalse);
      expect(_sortedDays(ghosts).last, horizonEnd);
      expect(ghosts.keys.length, 34); // Oct 1..31 + Nov 1..3
    });

    test('an explicit horizonDays pins the bound for deterministic tests', () {
      final task = _task('a', recurring: '0 8 * * *');

      final ghosts = expandRecurringForRange(
        [task],
        DateTime(2026, 8, 1),
        DateTime(2026, 8, 31),
        now: DateTime(2026, 8, 1),
        horizonDays: 3,
      );

      expect(ghosts.keys.toSet(), {
        DateTime(2026, 8, 1),
        DateTime(2026, 8, 2),
        DateTime(2026, 8, 3),
        DateTime(2026, 8, 4),
      });
    });

    test('horizonDays: 0 projects today and nothing else', () {
      final task = _task('a', recurring: '0 8 * * *');

      final ghosts = expandRecurringForRange(
        [task],
        DateTime(2026, 8, 1),
        DateTime(2026, 8, 31),
        now: DateTime(2026, 8, 1),
        horizonDays: 0,
      );

      expect(ghosts.keys.toList(), [DateTime(2026, 8, 1)]);
    });

    test('a negative horizonDays clamps to 0 rather than inverting', () {
      final task = _task('a', recurring: '0 8 * * *');

      final ghosts = expandRecurringForRange(
        [task],
        DateTime(2026, 8, 1),
        DateTime(2026, 8, 31),
        now: DateTime(2026, 8, 1),
        horizonDays: -5,
      );

      expect(ghosts.keys.toList(), [DateTime(2026, 8, 1)]);
    });

    test(
      'kMaxGhostsPerTask clamps the WINDOW, so an absurd horizon still '
      'yields a contiguous run with no mid-range hole',
      () {
        // The two bounds must never disagree: the old in-loop counter cut a
        // 92-day window at 60, leaving the last month of the range blank —
        // indistinguishable from a bug. Clamping the window instead means
        // whatever we return is always gap-free.
        final task = _task('a', recurring: '0 8 * * *');
        final now = DateTime(2026, 8, 1);

        final ghosts = expandRecurringForRange(
          [task],
          DateTime(2026, 8, 1),
          DateTime(2030, 8, 1),
          now: now,
          horizonDays: 100000,
        );

        final days = _sortedDays(ghosts);
        expect(days.length, kMaxGhostsPerTask);
        expect(days.first, now);
        for (var i = 1; i < days.length; i++) {
          expect(
            days[i],
            DateTime(now.year, now.month, now.day + i),
            reason: 'ghost days must be contiguous — no silent hole',
          );
        }
      },
    );

    test(
      'the horizon composes with the past-clamp: a range around today is '
      'bounded on BOTH sides',
      () {
        final task = _task('a', recurring: '0 8 * * *');
        final now = DateTime(2026, 8, 15);

        final ghosts = expandRecurringForRange(
          [task],
          DateTime(2026, 1, 1),
          DateTime(2027, 12, 31),
          now: now,
          horizonDays: 5,
        );

        expect(_sortedDays(ghosts).first, now);
        expect(_sortedDays(ghosts).last, DateTime(2026, 8, 20));
      },
    );
  });

  // ── recurUntil x horizon: whichever ends FIRST wins ──────────────────────
  group('expandRecurringForRange — recurUntil vs horizon', () {
    test('recurUntil earlier than the horizon wins', () {
      final task =
          _task('a', recurring: '0 8 * * *', recurUntil: '2026-08-05');

      final ghosts = expandRecurringForRange(
        [task],
        DateTime(2026, 8, 1),
        DateTime(2026, 12, 31),
        now: DateTime(2026, 8, 1),
        horizonDays: 30,
      );

      expect(_sortedDays(ghosts).last, DateTime(2026, 8, 5));
      expect(ghosts.keys.length, 5);
    });

    test('a horizon earlier than recurUntil wins', () {
      final task =
          _task('a', recurring: '0 8 * * *', recurUntil: '2026-12-31');

      final ghosts = expandRecurringForRange(
        [task],
        DateTime(2026, 8, 1),
        DateTime(2026, 12, 31),
        now: DateTime(2026, 8, 1),
        horizonDays: 3,
      );

      expect(_sortedDays(ghosts).last, DateTime(2026, 8, 4));
      expect(ghosts.keys.length, 4);
    });

    test('the caller\'s own rangeEnd still wins when it is the tightest', () {
      final task =
          _task('a', recurring: '0 8 * * *', recurUntil: '2026-12-31');

      final ghosts = expandRecurringForRange(
        [task],
        DateTime(2026, 8, 1),
        DateTime(2026, 8, 2),
        now: DateTime(2026, 8, 1),
        horizonDays: 90,
      );

      expect(ghosts.keys.toSet(),
          {DateTime(2026, 8, 1), DateTime(2026, 8, 2)});
    });
  });

  // ── Completed occurrences ───────────────────────────────────────────────
  //
  // On completion the server respawns the NEXT occurrence as a brand-new row
  // that carries the same cron (`tasks/store.py`), while the completed row
  // KEEPS its `recurring` value. Both rows reach the client, so a done
  // occurrence that still projected would double-dot every future match day
  // for what is one single series.
  group('expandRecurringForRange — done occurrences do not project', () {
    test('a done recurring task yields no ghosts', () {
      final task = _task('a', recurring: '0 8 * * *', status: 'done');

      final ghosts = expandRecurringForRange(
        [task],
        DateTime(2026, 8, 1),
        DateTime(2026, 8, 7),
        now: DateTime(2026, 8, 1),
      );

      expect(ghosts, isEmpty);
    });

    test(
      'a completed occurrence plus its respawned successor ghosts ONCE per '
      'day, not twice',
      () {
        final completed = _task(
          'old',
          dueDate: '2026-08-01',
          recurring: '0 8 * * *',
          status: 'done',
        );
        final successor =
            _task('new', dueDate: '2026-08-02', recurring: '0 8 * * *');

        final ghosts = expandRecurringForRange(
          [completed, successor],
          DateTime(2026, 8, 1),
          DateTime(2026, 8, 5),
          now: DateTime(2026, 8, 1),
        );

        // Aug 2 is the successor's own materialised day → deduped away.
        expect(ghosts.containsKey(DateTime(2026, 8, 2)), isFalse);
        for (final day in ghosts.keys) {
          expect(ghosts[day]!.map((t) => t.id), ['new']);
        }
      },
    );
  });

  // ── Day stepping stays on calendar days across a DST shift ──────────────
  group('expandRecurringForRange — DST-safe day stepping', () {
    test(
      'every ghost key is a local midnight, even across the autumn DST '
      'change',
      () {
        // Europe/Madrid rolls back on 2026-10-25, so stepping by a fixed
        // 24h Duration lands on 23:00 of the SAME day and every key after it
        // is an hour off local midnight — the view looks its ghosts up by
        // `DateTime(y, m, d)`, so those entries become invisible AND the last
        // day of the range gets skipped. NOTE: this assertion only bites in a
        // DST-observing zone (the dev/CI machine is Europe/Madrid); it is a
        // harmless pass under UTC.
        final task = _task('a', recurring: '0 8 * * *');

        final ghosts = expandRecurringForRange(
          [task],
          DateTime(2026, 10, 1),
          DateTime(2026, 10, 31),
          now: DateTime(2026, 10, 1),
        );

        expect(ghosts.keys.length, 31);
        expect(ghosts.containsKey(DateTime(2026, 10, 31)), isTrue);
        for (final day in ghosts.keys) {
          expect(day, DateTime(day.year, day.month, day.day),
              reason: 'ghost keys must be exact local midnights');
        }
      },
    );
  });
}
