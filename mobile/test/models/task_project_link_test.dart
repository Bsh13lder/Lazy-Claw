// Unit tests for the pure helper joining a Task to a Project by category —
// the same category<->name_key join the server and agent use, backing the
// expense detail sheet's task picker (only tasks that belong to the
// expense's project should be selectable).

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/models/task_project_link.dart';

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

Project _project(String id, String name, {String? nameKey}) => Project(
      id: id,
      name: name,
      nameKey: nameKey,
      budget: 0,
      currency: 'USD',
      status: 'active',
    );

void main() {
  group('tasksForProject', () {
    test('matches by category casefolded against name_key', () {
      final p = _project('p1', 'ClubBay', nameKey: 'clubbay');
      final tasks = [
        _task('a', category: 'ClubBay'),
        _task('b', category: 'clubbay'),
        _task('c', category: '  CLUBBAY  '),
      ];

      final result = tasksForProject(tasks, p);

      expect(result.map((t) => t.id), ['a', 'b', 'c']);
    });

    test('ignores tasks belonging to a different category', () {
      final p = _project('p1', 'ClubBay', nameKey: 'clubbay');
      final tasks = [
        _task('a', category: 'clubbay'),
        _task('b', category: 'marketing'),
        _task('c', category: 'Operations'),
      ];

      final result = tasksForProject(tasks, p);

      expect(result.map((t) => t.id), ['a']);
    });

    test('excludes tasks with a null category', () {
      final p = _project('p1', 'ClubBay', nameKey: 'clubbay');
      final tasks = [
        _task('a', category: 'clubbay'),
        _task('b', category: null),
      ];

      final result = tasksForProject(tasks, p);

      expect(result.map((t) => t.id), ['a']);
    });

    test('excludes tasks with a blank/whitespace-only category', () {
      final p = _project('p1', 'ClubBay', nameKey: 'clubbay');
      final tasks = [
        _task('a', category: 'clubbay'),
        _task('b', category: '   '),
      ];

      final result = tasksForProject(tasks, p);

      expect(result.map((t) => t.id), ['a']);
    });

    test('falls back to the lower-cased name when name_key is null', () {
      final p = _project('p1', 'Marketing', nameKey: null);
      final tasks = [
        _task('a', category: 'Marketing'),
        _task('b', category: 'marketing'),
        _task('c', category: 'operations'),
      ];

      final result = tasksForProject(tasks, p);

      expect(result.map((t) => t.id), ['a', 'b']);
    });

    test('empty task list yields empty result', () {
      final p = _project('p1', 'ClubBay', nameKey: 'clubbay');
      expect(tasksForProject(const [], p), isEmpty);
    });

    test('excludes done tasks by default', () {
      final p = _project('p1', 'ClubBay', nameKey: 'clubbay');
      final tasks = [
        _task('a', category: 'clubbay', status: 'todo'),
        _task('b', category: 'clubbay', status: 'done'),
        _task('c', category: 'clubbay', status: 'in_progress'),
      ];

      final result = tasksForProject(tasks, p);

      expect(result.map((t) => t.id), ['a', 'c']);
    });

    test('includes done tasks when includeCompleted is true', () {
      final p = _project('p1', 'ClubBay', nameKey: 'clubbay');
      final tasks = [
        _task('a', category: 'clubbay', status: 'todo'),
        _task('b', category: 'clubbay', status: 'done'),
      ];

      final result =
          tasksForProject(tasks, p, includeCompleted: true);

      expect(result.map((t) => t.id), ['a', 'b']);
    });
  });
}
