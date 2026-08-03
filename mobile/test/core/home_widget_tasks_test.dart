// Unit tests for the PURE selection + labeling helpers in
// core/home_widget_tasks.dart. The plugin-touching `updateTasksWidget` /
// `clearTasksWidget` are not exercised here (they require a platform channel);
// the selection logic that decides WHAT crosses the crypto boundary is.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/core/home_widget_tasks.dart';
import 'package:lazyclaw_mobile/models/task.dart';

Task _task({
  required String id,
  String title = 'T',
  String? dueDate,
  String status = 'todo',
}) =>
    Task(
      id: id,
      userId: 'u1',
      title: title,
      priority: 'medium',
      status: status,
      owner: 'user',
      nagCount: 0,
      createdAt: '2026-06-01T00:00:00',
      dueDate: dueDate,
    );

void main() {
  final now = DateTime(2026, 6, 7, 12, 0, 0); // Jun 7, noon.

  group('pickWidgetTasks', () {
    test('excludes done tasks', () {
      final tasks = [
        _task(id: 'a', dueDate: '2026-06-07', status: 'done'),
        _task(id: 'b', dueDate: '2026-06-08'),
      ];
      final picked = pickWidgetTasks(tasks, now: now);
      expect(picked.map((t) => t.id), ['b']);
    });

    test(
        'due-now no longer hides every future task — a spare row fills with '
        'the soonest upcoming task', () {
      final tasks = [
        _task(id: 'future', dueDate: '2026-06-20'),
        _task(id: 'today', dueDate: '2026-06-07T09:00:00'),
        _task(id: 'overdue', dueDate: '2026-06-01'),
      ];
      final picked = pickWidgetTasks(tasks, now: now);
      expect(picked.map((t) => t.id), ['overdue', 'today', 'future']);
    });

    test(
        '3 due-now + 2 future + 4 undated, 3-row cap: rows are exactly the '
        '3 due-now tasks and the footer counts everything hidden (+6 more)',
        () {
      final tasks = [
        _task(id: 'overdue', dueDate: '2026-06-01'),
        _task(id: 'today_am', dueDate: '2026-06-07T08:00:00'),
        _task(id: 'today_pm', dueDate: '2026-06-07T17:00:00'),
        _task(id: 'future1', dueDate: '2026-06-09'),
        _task(id: 'future2', dueDate: '2026-06-10'),
        for (var i = 0; i < 4; i++) _task(id: 'undated$i'),
      ];
      final picked = pickWidgetTasks(tasks, now: now);
      expect(
        picked.map((t) => t.id),
        ['overdue', 'today_am', 'today_pm'],
      );
      final tier = relevantWidgetTasks(tasks, now: now);
      expect(widgetMoreLabel(tier.length), '+6 more');
    });

    test(
        '1 due-now + 2 future: rows are due-now then the 2 soonest future, '
        'and nothing is hidden (empty footer)', () {
      final tasks = [
        _task(id: 'overdue', dueDate: '2026-06-01'),
        _task(id: 'future_far', dueDate: '2026-06-20'),
        _task(id: 'future_near', dueDate: '2026-06-09'),
      ];
      final picked = pickWidgetTasks(tasks, now: now);
      expect(
        picked.map((t) => t.id),
        ['overdue', 'future_near', 'future_far'],
      );
      final tier = relevantWidgetTasks(tasks, now: now);
      expect(widgetMoreLabel(tier.length), '');
    });

    test('overdue + today sort soonest-first within the tier', () {
      final tasks = [
        _task(id: 'today_pm', dueDate: '2026-06-07T17:00:00'),
        _task(id: 'today_am', dueDate: '2026-06-07T09:00:00'),
        _task(id: 'overdue', dueDate: '2026-06-01'),
      ];
      final picked = pickWidgetTasks(tasks, now: now);
      expect(picked.map((t) => t.id), ['overdue', 'today_am', 'today_pm']);
    });

    test(
        'upcoming tasks fill first (soonest first), then undated tasks fill '
        'any remaining rows', () {
      final tasks = [
        _task(id: 'later', dueDate: '2026-06-20'),
        _task(id: 'sooner', dueDate: '2026-06-09'),
        _task(id: 'undated'),
      ];
      final picked = pickWidgetTasks(tasks, now: now);
      expect(picked.map((t) => t.id), ['sooner', 'later', 'undated']);
    });

    test('falls back to undated open tasks when no dated tasks exist', () {
      final tasks = [
        _task(id: 'undated1'),
        _task(id: 'undated2'),
        _task(id: 'done', dueDate: null, status: 'done'),
      ];
      final picked = pickWidgetTasks(tasks, now: now);
      expect(picked.map((t) => t.id), ['undated1', 'undated2']);
    });

    test('caps at 3 rows', () {
      final tasks = List.generate(
        6,
        (i) => _task(id: 'task$i', dueDate: '2026-06-${10 + i}'),
      );
      final picked = pickWidgetTasks(tasks, now: now);
      expect(picked.length, kTasksWidgetRowCount);
      expect(picked.length, 3);
      expect(picked.map((t) => t.id), ['task0', 'task1', 'task2']);
    });

    test('empty in → empty out', () {
      expect(pickWidgetTasks(const [], now: now), isEmpty);
    });
  });

  group('relevantWidgetTasks (uncapped — drives the "+N more" footer)', () {
    test(
        'returns EVERY open task (due-now + upcoming + undated) so the '
        'footer always counts hidden future/undated work', () {
      final tasks = [
        for (var i = 0; i < 5; i++)
          _task(id: 'today$i', dueDate: '2026-06-07T0$i:00:00'),
        _task(id: 'future', dueDate: '2026-06-20'),
      ];
      final tier = relevantWidgetTasks(tasks, now: now);
      // The future task is hidden by the 3-row cap but must still be
      // counted — that's the whole point of the fix.
      expect(tier.length, 6);
    });

    test('returns the upcoming tier when today is clear', () {
      final tasks = [
        _task(id: 'a', dueDate: '2026-06-09'),
        _task(id: 'b', dueDate: '2026-06-10'),
      ];
      expect(relevantWidgetTasks(tasks, now: now).length, 2);
    });
  });

  group('widgetDueLabel', () {
    test('timed due → day word + clock (the task\'s full date and time)', () {
      expect(
        widgetDueLabel(_task(id: 'x', dueDate: '2026-06-07T17:00:00'),
            now: now),
        'Today · 5:00 PM',
      );
      expect(
        widgetDueLabel(_task(id: 'x', dueDate: '2026-06-08T09:00:00'),
            now: now),
        'Tomorrow · 9:00 AM',
      );
      expect(
        widgetDueLabel(_task(id: 'x', dueDate: '2026-06-15T17:30:00'),
            now: now),
        'Jun 15 · 5:30 PM',
      );
      expect(
        widgetDueLabel(_task(id: 'x', dueDate: '2026-06-06T22:00:00'),
            now: now),
        'Yesterday · 10:00 PM',
      );
    });

    test('date-only today / tomorrow / yesterday → relative words', () {
      expect(widgetDueLabel(_task(id: 'x', dueDate: '2026-06-07'), now: now),
          'Today');
      expect(widgetDueLabel(_task(id: 'x', dueDate: '2026-06-08'), now: now),
          'Tomorrow');
      expect(widgetDueLabel(_task(id: 'x', dueDate: '2026-06-06'), now: now),
          'Yesterday');
    });

    test('date-only further out → "<Mon D>"', () {
      expect(widgetDueLabel(_task(id: 'x', dueDate: '2026-06-15'), now: now),
          'Jun 15');
    });

    test('no due → empty string', () {
      expect(widgetDueLabel(_task(id: 'x'), now: now), '');
    });
  });

  group('.toLocal() parity (D1 mirror — matches task_calendar_utils.dart)', () {
    // 2026-08-04T22:00:00Z is 2026-08-05T00:00:00 in Europe/Madrid (this
    // worktree/CI's local TZ, +2h CEST) — i.e. LOCAL tomorrow relative to
    // `now`. Reading .year/.month/.day off the raw UTC parse instead gives
    // Aug 4 (today), which wrongly lands the task in the due-now tier.
    final localMidnightCrossNow = DateTime(2026, 8, 4, 10, 0, 0);

    test(
      'relevantWidgetTasks: a UTC-aware due date crossing local midnight '
      'lands in "upcoming", not "due now" — and so does not suppress other '
      'future tasks',
      () {
        final tasks = [
          _task(id: 'crosses', dueDate: '2026-08-04T22:00:00Z'),
          _task(id: 'far', dueDate: '2026-08-10'),
        ];

        final tier =
            relevantWidgetTasks(tasks, now: localMidnightCrossNow);

        // Without `.toLocal()`, 'crosses' is misread as due-now, the
        // dueNow tier becomes non-empty, and 'far' is dropped entirely
        // (exactly the "any due-now task hides every future task" bug).
        expect(tier.map((t) => t.id).toList(), ['crosses', 'far']);
      },
    );

    test(
      'widgetDueLabel: the same crossing due date reads as "Tomorrow", not '
      '"Today"',
      () {
        final task = _task(id: 'crosses', dueDate: '2026-08-04T22:00:00Z');
        expect(
          widgetDueLabel(task, now: localMidnightCrossNow),
          startsWith('Tomorrow'),
        );
      },
    );
  });

  group('widgetUpdatedStamp', () {
    test('zero-pads to 24h HH:mm', () {
      expect(widgetUpdatedStamp(DateTime(2026, 6, 10, 9, 5)), '09:05');
      expect(widgetUpdatedStamp(DateTime(2026, 6, 10, 19, 42)), '19:42');
      expect(widgetUpdatedStamp(DateTime(2026, 6, 10, 0, 0)), '00:00');
    });
  });

  group('widgetMoreLabel', () {
    test('empty when open tasks fit the rows', () {
      expect(widgetMoreLabel(0), '');
      expect(widgetMoreLabel(3), '');
    });

    test('counts only the overflow beyond the rows', () {
      expect(widgetMoreLabel(4), '+1 more');
      expect(widgetMoreLabel(10), '+7 more');
    });
  });
}
