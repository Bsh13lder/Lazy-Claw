// Unit tests for the pure helpers backing the Tasks "Projects" view.
//
// Framework-light (no widget pump): bucketing tasks under their project by a
// case-insensitive category→name match, an Uncategorized catch-all, ordering
// the group keys, and open/total counts.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_project_grouping.dart';

Task _task(
  String id, {
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
  group('groupTasksByProject', () {
    test('matches category → project name, case-insensitive', () {
      final tasks = [
        _task('a', category: 'Home'),
        _task('b', category: 'home'),
        _task('c', category: 'HOME'),
      ];
      final projects = [_project('p1', 'Home')];

      final groups = groupTasksByProject(tasks, projects);

      expect(groups.keys, ['Home']);
      expect(groups['Home']!.map((t) => t.id), containsAll(['a', 'b', 'c']));
    });

    test('seeds an empty group for every project (zero-task projects appear)',
        () {
      final groups = groupTasksByProject(
        const [],
        [_project('p1', 'Home'), _project('p2', 'Work')],
      );

      expect(groups.keys, containsAll(['Home', 'Work']));
      expect(groups['Home'], isEmpty);
      expect(groups['Work'], isEmpty);
    });

    test('null / blank category → Uncategorized bucket', () {
      final tasks = [
        _task('a', category: null),
        _task('b', category: ''),
        _task('c', category: '   '),
      ];

      final groups = groupTasksByProject(tasks, const []);

      expect(groups.keys, [kUncategorizedProjectLabel]);
      expect(groups[kUncategorizedProjectLabel]!.map((t) => t.id),
          containsAll(['a', 'b', 'c']));
    });

    test('a category with no matching project gets its own group', () {
      final tasks = [_task('a', category: 'Garden')];
      final projects = [_project('p1', 'Home')];

      final groups = groupTasksByProject(tasks, projects);

      // Home (seeded, empty) + Garden (category-only).
      expect(groups.keys, containsAll(['Home', 'Garden']));
      expect(groups['Home'], isEmpty);
      expect(groups['Garden']!.map((t) => t.id), ['a']);
    });
  });

  group('orderedProjectGroupNames', () {
    test('projects first (in order), extras sorted, Uncategorized last', () {
      final projects = [_project('p1', 'Work'), _project('p2', 'Home')];
      final groups = {
        'Home': <Task>[],
        'Work': <Task>[],
        'Zebra': <Task>[_task('z', category: 'Zebra')],
        'Apple': <Task>[_task('a', category: 'Apple')],
        kUncategorizedProjectLabel: <Task>[_task('u')],
      };

      final ordered = orderedProjectGroupNames(projects, groups);

      expect(ordered, ['Work', 'Home', 'Apple', 'Zebra', 'Uncategorized']);
    });

    test('omits Uncategorized when there is no such bucket', () {
      final projects = [_project('p1', 'Home')];
      final groups = {'Home': <Task>[]};

      expect(orderedProjectGroupNames(projects, groups), ['Home']);
    });
  });

  group('projectGroupCounts', () {
    test('counts open (not done) and total', () {
      final counts = projectGroupCounts([
        _task('a', status: 'todo'),
        _task('b', status: 'in_progress'),
        _task('c', status: 'done'),
      ]);
      expect(counts.open, 2);
      expect(counts.total, 3);
    });

    test('is all-zero for an empty group', () {
      final counts = projectGroupCounts(const []);
      expect(counts.open, 0);
      expect(counts.total, 0);
    });
  });
}
