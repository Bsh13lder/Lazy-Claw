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

    test('a "Today" widget NEVER shows future tasks when something is due now',
        () {
      final tasks = [
        _task(id: 'future', dueDate: '2026-06-20'),
        _task(id: 'today', dueDate: '2026-06-07T09:00:00'),
        _task(id: 'overdue', dueDate: '2026-06-01'),
      ];
      final picked = pickWidgetTasks(tasks, now: now);
      expect(picked.map((t) => t.id), ['overdue', 'today']);
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

    test('falls back to soonest upcoming when nothing is due today', () {
      final tasks = [
        _task(id: 'later', dueDate: '2026-06-20'),
        _task(id: 'sooner', dueDate: '2026-06-09'),
        _task(id: 'undated'),
      ];
      final picked = pickWidgetTasks(tasks, now: now);
      expect(picked.map((t) => t.id), ['sooner', 'later']);
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

  group('relevantWidgetTasks (uncapped tier — drives the "+N more" footer)', () {
    test('returns the FULL today tier so the footer counts today overflow only',
        () {
      final tasks = [
        for (var i = 0; i < 5; i++)
          _task(id: 'today$i', dueDate: '2026-06-07T0$i:00:00'),
        _task(id: 'future', dueDate: '2026-06-20'),
      ];
      final tier = relevantWidgetTasks(tasks, now: now);
      expect(tier.length, 5); // future task NOT counted in "+N more"
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
    test('timed due → clock label only', () {
      expect(
        widgetDueLabel(_task(id: 'x', dueDate: '2026-06-07T17:00:00'),
            now: now),
        '5:00 PM',
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
