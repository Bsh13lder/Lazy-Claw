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

    test('orders dated tasks soonest-first (overdue/today before upcoming)', () {
      final tasks = [
        _task(id: 'future', dueDate: '2026-06-20'),
        _task(id: 'today', dueDate: '2026-06-07T09:00:00'),
        _task(id: 'overdue', dueDate: '2026-06-01'),
      ];
      final picked = pickWidgetTasks(tasks, now: now);
      expect(picked.map((t) => t.id), ['overdue', 'today', 'future']);
    });

    test('undated tasks sort AFTER all dated tasks', () {
      final tasks = [
        _task(id: 'undated1'),
        _task(id: 'dated', dueDate: '2026-06-09'),
        _task(id: 'undated2'),
      ];
      final picked = pickWidgetTasks(tasks, now: now);
      expect(picked.map((t) => t.id), ['dated', 'undated1', 'undated2']);
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
}
