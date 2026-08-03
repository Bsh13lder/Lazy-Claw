// Unit tests for the pure helpers backing the Tasks "Projects" view.
//
// Framework-light (no widget pump): bucketing tasks under their project by a
// case-insensitive category→name match, a first-class Inbox catch-all, ordering
// the group keys, and open/total counts.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_project_grouping.dart';

Task _task(String id, {String? category, String status = 'todo'}) => Task(
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

      // Home + the always-seeded Inbox bucket.
      expect(groups.keys, containsAll(['Home', kInboxProjectLabel]));
      expect(groups['Home']!.map((t) => t.id), containsAll(['a', 'b', 'c']));
      expect(groups[kInboxProjectLabel], isEmpty);
    });

    test(
      'seeds an empty group for every project (zero-task projects appear)',
      () {
        final groups = groupTasksByProject(const [], [
          _project('p1', 'Home'),
          _project('p2', 'Work'),
        ]);

        expect(groups.keys, containsAll(['Home', 'Work']));
        expect(groups['Home'], isEmpty);
        expect(groups['Work'], isEmpty);
      },
    );

    test('null / blank category → Inbox bucket', () {
      final tasks = [
        _task('a', category: null),
        _task('b', category: ''),
        _task('c', category: '   '),
      ];

      final groups = groupTasksByProject(tasks, const []);

      // Inbox is the only bucket here (no projects).
      expect(groups.keys, [kInboxProjectLabel]);
      expect(
        groups[kInboxProjectLabel]!.map((t) => t.id),
        containsAll(['a', 'b', 'c']),
      );
    });

    test('Inbox bucket is always seeded, even with no projectless tasks', () {
      final groups = groupTasksByProject(
        [_task('a', category: 'Home')],
        [_project('p1', 'Home')],
      );

      // Inbox is present (and empty) so it's a permanent home for loose tasks.
      expect(groups.containsKey(kInboxProjectLabel), isTrue);
      expect(groups[kInboxProjectLabel], isEmpty);
    });

    test('a category with no matching project gets its own group', () {
      final tasks = [_task('a', category: 'Garden')];
      final projects = [_project('p1', 'Home')];

      final groups = groupTasksByProject(tasks, projects);

      // Home (seeded, empty) + Garden (category-only) + Inbox (seeded, empty).
      expect(groups.keys, containsAll(['Home', 'Garden', kInboxProjectLabel]));
      expect(groups['Home'], isEmpty);
      expect(groups['Garden']!.map((t) => t.id), ['a']);
    });
  });

  group('orderedProjectGroupNames', () {
    test('Inbox first, then projects (in order), then extras sorted', () {
      final projects = [_project('p1', 'Work'), _project('p2', 'Home')];
      final groups = {
        kInboxProjectLabel: <Task>[_task('u')],
        'Home': <Task>[],
        'Work': <Task>[],
        'Zebra': <Task>[_task('z', category: 'Zebra')],
        'Apple': <Task>[_task('a', category: 'Apple')],
      };

      final ordered = orderedProjectGroupNames(projects, groups);

      expect(ordered, ['Inbox', 'Work', 'Home', 'Apple', 'Zebra']);
    });

    test('omits Inbox when there is no such bucket', () {
      final projects = [_project('p1', 'Home')];
      final groups = {'Home': <Task>[]};

      expect(orderedProjectGroupNames(projects, groups), ['Home']);
    });

    test('Inbox leads even when it is the only bucket', () {
      final groups = {
        kInboxProjectLabel: <Task>[_task('u')],
      };
      expect(orderedProjectGroupNames(const [], groups), ['Inbox']);
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

  group('splitTasksByGroup', () {
    test(
      'a task matching a project lands in realProjects (case-insensitive)',
      () {
        final tasks = [
          _task('a', category: 'home'),
          _task('b', category: 'HOME'),
        ];
        final projects = [_project('p1', 'Home', color: '#FF0000')];

        final split = splitTasksByGroup(tasks, projects);

        expect(split.realProjects, hasLength(1));
        final home = split.realProjects.single;
        expect(home.name, 'Home'); // canonical project casing
        expect(home.project.color, '#FF0000'); // carried for the color dot
        expect(home.tasks.map((t) => t.id), containsAll(['a', 'b']));
        expect(split.tags, isEmpty);
        expect(split.inbox, isEmpty);
      },
    );

    test('a task with an unknown category becomes a tag (not a project)', () {
      final tasks = [
        _task('a', category: 'Garden'),
        _task('b', category: 'Garden'),
      ];
      final projects = [_project('p1', 'Home')];

      final split = splitTasksByGroup(tasks, projects);

      // Home is seeded (zero tasks) under realProjects, Garden is a tag.
      expect(split.realProjects.map((b) => b.name), ['Home']);
      expect(split.realProjects.single.tasks, isEmpty);
      expect(split.tags, hasLength(1));
      expect(split.tags.single.name, 'Garden');
      expect(split.tags.single.tasks.map((t) => t.id), containsAll(['a', 'b']));
      expect(split.inbox, isEmpty);
    });

    test('blank / null category lands in inbox', () {
      final tasks = [
        _task('a', category: null),
        _task('b', category: ''),
        _task('c', category: '   '),
      ];

      final split = splitTasksByGroup(tasks, const []);

      expect(split.inbox.map((t) => t.id), containsAll(['a', 'b', 'c']));
      expect(split.realProjects, isEmpty);
      expect(split.tags, isEmpty);
    });

    test('zero-task projects are still seeded in realProjects', () {
      final split = splitTasksByGroup(const [], [
        _project('p1', 'Home'),
        _project('p2', 'Work'),
      ]);

      expect(split.realProjects.map((b) => b.name), ['Home', 'Work']);
      expect(split.realProjects.every((b) => b.tasks.isEmpty), isTrue);
      expect(split.tags, isEmpty);
      expect(split.inbox, isEmpty);
      expect(split.hasRealProjects, isTrue);
      expect(split.hasTags, isFalse);
    });

    test('realProjects preserves the projects-list order', () {
      final split = splitTasksByGroup(const [], [
        _project('p1', 'Work'),
        _project('p2', 'Home'),
        _project('p3', 'Side'),
      ]);

      expect(split.realProjects.map((b) => b.name), ['Work', 'Home', 'Side']);
    });

    test('tags are sorted alphabetically (case-insensitively)', () {
      final tasks = [
        _task('z', category: 'Zebra'),
        _task('a', category: 'apple'),
        _task('m', category: 'Mango'),
      ];

      final split = splitTasksByGroup(tasks, const []);

      expect(split.tags.map((b) => b.name), ['apple', 'Mango', 'Zebra']);
    });

    test('only tags with tasks appear (no empty tag buckets)', () {
      final split = splitTasksByGroup(
        [_task('a', category: 'Home')],
        [_project('p1', 'Home')],
      );

      // Home matched → realProjects; nothing left over → no tags.
      expect(split.tags, isEmpty);
      expect(split.hasTags, isFalse);
    });

    test('hasTags / hasRealProjects reflect content', () {
      final split = splitTasksByGroup([
        _task('a', category: 'Garden'),
      ], const []);
      expect(split.hasRealProjects, isFalse);
      expect(split.hasTags, isTrue);
    });
  });
}
