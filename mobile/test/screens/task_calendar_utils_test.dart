// Unit tests for the pure calendar helpers backing the Tasks calendar view.
//
// These are intentionally framework-light (no widget pump): grouping tasks by
// due-day, building a name→color lookup from projects, and resolving a single
// task's accent color. Keeping them pure makes the calendar's coloring logic
// trivially testable and decoupled from TableCalendar.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_calendar_utils.dart';

Task _task(
  String id, {
  String? dueDate,
  String? category,
  String status = 'todo',
}) =>
    Task(
      id: id,
      userId: 'u1',
      title: 'Task $id',
      category: category,
      priority: 'medium',
      status: status,
      owner: 'user',
      dueDate: dueDate,
      nagCount: 0,
      createdAt: '2026-06-06T00:00:00Z',
    );

Project _project(String id, String name, {String? color}) => Project(
      id: id,
      name: name,
      budget: 0,
      currency: 'USD',
      status: 'active',
      color: color,
    );

void main() {
  group('groupTasksByDay', () {
    test('groups tasks by their due date (date-only key)', () {
      final tasks = [
        _task('a', dueDate: '2026-06-10'),
        _task('b', dueDate: '2026-06-10'),
        _task('c', dueDate: '2026-06-11'),
      ];

      final grouped = groupTasksByDay(tasks);

      expect(grouped.keys.length, 2);
      expect(grouped[DateTime(2026, 6, 10)]!.map((t) => t.id),
          containsAll(['a', 'b']));
      expect(grouped[DateTime(2026, 6, 11)]!.map((t) => t.id), ['c']);
    });

    test('drops tasks with no due date', () {
      final tasks = [
        _task('a', dueDate: '2026-06-10'),
        _task('b'), // no due date
        _task('c', dueDate: null),
      ];

      final grouped = groupTasksByDay(tasks);

      expect(grouped.keys.length, 1);
      expect(grouped[DateTime(2026, 6, 10)]!.map((t) => t.id), ['a']);
    });

    test('ignores the time component when bucketing', () {
      // Naive (no zone suffix) strings are already local, so `.toLocal()` is
      // a no-op here — this test is purely about the time-of-day being
      // dropped from the key, not about zone conversion (see the dedicated
      // `.toLocal()` group below for that).
      final tasks = [
        _task('a', dueDate: '2026-06-10T08:30:00'),
        _task('b', dueDate: '2026-06-10T23:59:59'),
      ];

      final grouped = groupTasksByDay(tasks);

      expect(grouped.keys.length, 1);
      final key = grouped.keys.first;
      expect(key.hour, 0);
      expect(key.minute, 0);
      expect(grouped[DateTime(2026, 6, 10)]!.map((t) => t.id),
          containsAll(['a', 'b']));
    });

    test('drops tasks whose due date is unparseable', () {
      final tasks = [
        _task('a', dueDate: 'not-a-date'),
        _task('b', dueDate: '2026-06-10'),
      ];

      final grouped = groupTasksByDay(tasks);

      expect(grouped.keys.length, 1);
      expect(grouped[DateTime(2026, 6, 10)]!.map((t) => t.id), ['b']);
    });

    test('a malformed due date is logged via debugPrint, not silently '
        'dropped', () {
      final messages = <String>[];
      final original = debugPrint;
      debugPrint = (String? message, {int? wrapWidth}) {
        messages.add(message ?? '');
      };
      try {
        final tasks = [
          _task('a', dueDate: 'not-a-date'),
          _task('b', dueDate: '2026-06-10'),
        ];

        final grouped = groupTasksByDay(tasks);

        expect(grouped.keys.length, 1); // still dropped from the calendar…
        expect(messages, isNotEmpty); // …but the failure is diagnosable.
        expect(messages.any((m) => m.contains('not-a-date')), isTrue);
        expect(messages.any((m) => m.contains('a')), isTrue); // task id
      } finally {
        debugPrint = original;
      }
    });
  });

  group('groupTasksByDay .toLocal() (D1 — UTC-aware due dates)', () {
    test('a UTC-aware due date keys to the LOCAL calendar day, not the UTC '
        'day', () {
      // 2026-08-04T00:00:00+02:00 is 2026-08-03T22:00:00Z. Reading
      // .year/.month/.day off the raw (UTC) parse gives Aug 3 — one day
      // early for a Madrid (or any UTC+ offset) user. `.toLocal()` must
      // resolve it back to Aug 4, matching the wall-clock date the user
      // actually set.
      final tasks = [_task('a', dueDate: '2026-08-04T00:00:00+02:00')];

      final grouped = groupTasksByDay(tasks);

      // The exact key depends on this worktree/CI machine's local
      // timezone (see due_date_test.dart for the same convention) — derive
      // the expected key the same way `.toLocal()` would, so the assertion
      // is a real behavioral check, not a tautology: it fails outright
      // without the .toLocal() fix on any machine set to a positive UTC
      // offset (e.g. Europe/Madrid, this worktree's TZ).
      final expectedLocalDay = DateTime.parse('2026-08-04T00:00:00+02:00')
          .toLocal();
      final expectedKey = DateTime(
        expectedLocalDay.year,
        expectedLocalDay.month,
        expectedLocalDay.day,
      );

      expect(grouped.keys.length, 1);
      expect(grouped.containsKey(expectedKey), isTrue);
      expect(grouped[expectedKey]!.map((t) => t.id), ['a']);
    });

    test('pinned to Europe/Madrid: keys to Aug 4, not Aug 3', () {
      // This worktree's machine (and CI) run with TZ=Europe/Madrid — pin the
      // exact day from the diagnosis report so a regression is unambiguous
      // rather than hidden behind the machine-relative assertion above.
      final localOffsetHours = DateTime.now().timeZoneOffset.inHours;
      if (localOffsetHours != 2 && localOffsetHours != 1) {
        // Not running under Europe/Madrid (CEST +2 / CET +1) — skip the
        // pinned assertion, the machine-relative test above still covers it.
        return;
      }
      final tasks = [_task('a', dueDate: '2026-08-04T00:00:00+02:00')];

      final grouped = groupTasksByDay(tasks);

      expect(grouped.keys.length, 1);
      expect(grouped.containsKey(DateTime(2026, 8, 4)), isTrue);
      expect(grouped.containsKey(DateTime(2026, 8, 3)), isFalse);
    });
  });

  group('projectColorMap', () {
    test('maps lowercased name → hex and skips null colors', () {
      final projects = [
        _project('1', 'Marketing', color: '#FF0000'),
        _project('2', 'Home', color: null),
        _project('3', 'Side Hustle', color: '#00FF00'),
      ];

      final map = projectColorMap(projects);

      expect(map, {
        'marketing': '#FF0000',
        'side hustle': '#00FF00',
      });
      expect(map.containsKey('home'), isFalse);
    });

    test('returns an empty map for no projects', () {
      expect(projectColorMap(const []), isEmpty);
    });
  });

  group('dayTaskCounts', () {
    test('splits open vs done by status', () {
      final tasks = [
        _task('a', status: 'todo'),
        _task('b', status: 'in_progress'),
        _task('c', status: 'done'),
      ];

      final counts = dayTaskCounts(tasks);

      expect(counts.open, 2);
      expect(counts.done, 1);
    });

    test('is all-zero for an empty day', () {
      final counts = dayTaskCounts(const []);
      expect(counts.open, 0);
      expect(counts.done, 0);
    });

    test('counts every task as done when all done', () {
      final counts = dayTaskCounts([
        _task('a', status: 'done'),
        _task('b', status: 'done'),
      ]);
      expect(counts.open, 0);
      expect(counts.done, 2);
    });
  });

  group('isDayAllDone', () {
    test('true only when there is >=1 task and all are done', () {
      expect(
        isDayAllDone([_task('a', status: 'done'), _task('b', status: 'done')]),
        isTrue,
      );
    });

    test('false when at least one task is still open', () {
      expect(
        isDayAllDone([_task('a', status: 'done'), _task('b', status: 'todo')]),
        isFalse,
      );
    });

    test('false for an empty day (nothing to clear)', () {
      expect(isDayAllDone(const []), isFalse);
    });

    test('false for a single open task', () {
      expect(isDayAllDone([_task('a', status: 'todo')]), isFalse);
    });
  });

  group('pickDayMarkerTasks', () {
    test('open tasks lead, done tasks trail', () {
      final tasks = [
        _task('done1', status: 'done'),
        _task('open1', status: 'todo'),
        _task('done2', status: 'done'),
        _task('open2', status: 'in_progress'),
      ];

      final picked = pickDayMarkerTasks(tasks, maxDots: 4);

      // Both open ids come before both done ids.
      expect(picked.shown.map((t) => t.id), ['open1', 'open2', 'done1', 'done2']);
      expect(picked.overflow, 0);
    });

    test('caps at maxDots and counts the remainder as overflow', () {
      final tasks = [
        for (var i = 0; i < 5; i++) _task('o$i', status: 'todo'),
      ];

      final picked = pickDayMarkerTasks(tasks, maxDots: 3);

      expect(picked.shown.length, 3);
      expect(picked.overflow, 2);
    });

    test('overflow respects open-first ordering (open shown before done)', () {
      final tasks = [
        _task('done1', status: 'done'),
        _task('done2', status: 'done'),
        _task('open1', status: 'todo'),
      ];

      final picked = pickDayMarkerTasks(tasks, maxDots: 2);

      // The single open task is shown first; one done task overflows.
      expect(picked.shown.map((t) => t.id), ['open1', 'done1']);
      expect(picked.overflow, 1);
    });

    test('empty day yields no dots and no overflow', () {
      final picked = pickDayMarkerTasks(const [], maxDots: 3);
      expect(picked.shown, isEmpty);
      expect(picked.overflow, 0);
    });

    test('does not mutate the input list', () {
      final tasks = [
        _task('done1', status: 'done'),
        _task('open1', status: 'todo'),
      ];
      final before = List.of(tasks);

      pickDayMarkerTasks(tasks, maxDots: 3);

      // Input order is untouched (a fresh ordered list is returned).
      expect(tasks.map((t) => t.id), before.map((t) => t.id));
    });
  });

  group('colorForTask', () {
    const fallback = Color(0xFF123456);

    test('matches category → project color, case-insensitive', () {
      final map = projectColorMap([_project('1', 'Marketing', color: '#FF0000')]);
      final task = _task('a', category: 'MARKETING');

      expect(colorForTask(task, map, fallback), const Color(0xFFFF0000));
    });

    test('parses a #RRGGBB hex into an opaque Color', () {
      final map = {'home': '#1A2B3C'};
      final task = _task('a', category: 'home');

      expect(colorForTask(task, map, fallback), const Color(0xFF1A2B3C));
    });

    test('falls back when the category has no matching project', () {
      final map = {'marketing': '#FF0000'};
      final task = _task('a', category: 'unknown');

      expect(colorForTask(task, map, fallback), fallback);
    });

    test('falls back when the task has no category', () {
      final map = {'marketing': '#FF0000'};
      final task = _task('a', category: null);

      expect(colorForTask(task, map, fallback), fallback);
    });

    test('falls back when the stored hex is malformed', () {
      final map = {'marketing': 'not-a-hex'};
      final task = _task('a', category: 'Marketing');

      expect(colorForTask(task, map, fallback), fallback);
    });
  });

  // Regression coverage for the 2026-08 "every day says ○ ○ ○ +37" report:
  // ghosts are speculative, never real work, so they must never inflate the
  // "+N" overflow badge, and at most ONE ghost marker should ever render
  // regardless of how many recurring tasks ghost on the same day.
  group('pickDayMarkers', () {
    test('2 real tasks + 40 ghosts: shows both real dots, exactly one ghost, '
        'and overflow reflects ONLY the real tasks (no +40)', () {
      final tasks = [
        _task('r1', status: 'todo'),
        _task('r2', status: 'todo'),
      ];
      final ghosts = [for (var i = 0; i < 40; i++) _task('g$i')];

      final picked = pickDayMarkers(tasks, ghosts, maxDots: 3);

      expect(picked.shown.map((t) => t.id), ['r1', 'r2']);
      expect(picked.ghost?.id, 'g0');
      expect(picked.overflow, 0);
    });

    test('maxDots real tasks (no free slot) render NO ghost marker even '
        'with many ghosts, and overflow still counts only the real '
        'overflow', () {
      final tasks = [for (var i = 0; i < 5; i++) _task('r$i', status: 'todo')];
      final ghosts = [for (var i = 0; i < 40; i++) _task('g$i')];

      final picked = pickDayMarkers(tasks, ghosts, maxDots: 3);

      expect(picked.shown.length, 3);
      expect(picked.ghost, isNull);
      expect(picked.overflow, 2); // 5 real - 3 shown, never +42
    });

    test('0 real + 5 ghosts renders exactly one ghost marker and no '
        'overflow badge', () {
      final ghosts = [for (var i = 0; i < 5; i++) _task('g$i')];

      final picked = pickDayMarkers(const [], ghosts, maxDots: 3);

      expect(picked.shown, isEmpty);
      expect(picked.ghost?.id, 'g0');
      expect(picked.overflow, 0);
    });

    test('no ghosts at all yields a null ghost regardless of free slots', () {
      final tasks = [_task('r1', status: 'todo')];

      final picked = pickDayMarkers(tasks, const [], maxDots: 3);

      expect(picked.ghost, isNull);
      expect(picked.overflow, 0);
    });

    test('exactly maxDots-1 real tasks still leaves exactly one ghost slot',
        () {
      final tasks = [
        _task('r1', status: 'todo'),
        _task('r2', status: 'todo'),
      ];
      final ghosts = [_task('g1'), _task('g2')];

      final picked = pickDayMarkers(tasks, ghosts, maxDots: 3);

      expect(picked.shown.length, 2);
      expect(picked.ghost?.id, 'g1'); // first ghost, deterministic order
      expect(picked.overflow, 0);
    });
  });
}
