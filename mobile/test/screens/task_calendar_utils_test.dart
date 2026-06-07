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
}) =>
    Task(
      id: id,
      userId: 'u1',
      title: 'Task $id',
      category: category,
      priority: 'medium',
      status: 'todo',
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
      final tasks = [
        _task('a', dueDate: '2026-06-10T08:30:00Z'),
        _task('b', dueDate: '2026-06-10T23:59:59Z'),
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
}
