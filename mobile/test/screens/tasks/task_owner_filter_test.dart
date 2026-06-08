// Unit tests for the pure owner-filter helper backing the Tasks "All · Mine ·
// AI" chip row. Framework-light (no widget pump): partitioning tasks by
// `task.owner`.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_owner_filter.dart';

Task _task(String id, {String owner = 'user'}) => Task(
      id: id,
      userId: 'u1',
      title: 'Task $id',
      priority: 'medium',
      status: 'todo',
      owner: owner,
      nagCount: 0,
      createdAt: '2026-06-06T00:00:00Z',
    );

void main() {
  group('isAgentTask', () {
    test('true for owner "agent"', () {
      expect(isAgentTask(_task('a', owner: 'agent')), isTrue);
    });

    test('false for owner "user"', () {
      expect(isAgentTask(_task('a', owner: 'user')), isFalse);
    });

    test('is case / whitespace tolerant (legacy rows)', () {
      expect(isAgentTask(_task('a', owner: ' Agent ')), isTrue);
      expect(isAgentTask(_task('a', owner: 'AGENT')), isTrue);
    });

    test('false for an unexpected owner string', () {
      expect(isAgentTask(_task('a', owner: 'system')), isFalse);
    });
  });

  group('filterByOwner', () {
    final tasks = [
      _task('u1', owner: 'user'),
      _task('a1', owner: 'agent'),
      _task('u2', owner: 'user'),
      _task('a2', owner: 'agent'),
    ];

    test('all → the list unchanged (same order)', () {
      final out = filterByOwner(tasks, TaskOwnerFilter.all);
      expect(out.map((t) => t.id), ['u1', 'a1', 'u2', 'a2']);
    });

    test('mine → only non-agent tasks', () {
      final out = filterByOwner(tasks, TaskOwnerFilter.mine);
      expect(out.map((t) => t.id), ['u1', 'u2']);
    });

    test('ai → only agent tasks', () {
      final out = filterByOwner(tasks, TaskOwnerFilter.ai);
      expect(out.map((t) => t.id), ['a1', 'a2']);
    });

    test('mine keeps tasks with an unexpected owner (never silently dropped)',
        () {
      final mixed = [_task('x', owner: 'system'), _task('a', owner: 'agent')];
      final out = filterByOwner(mixed, TaskOwnerFilter.mine);
      expect(out.map((t) => t.id), ['x']);
    });

    test('preserves input order within a filter', () {
      final out = filterByOwner(tasks, TaskOwnerFilter.ai);
      expect(out.first.id, 'a1');
      expect(out.last.id, 'a2');
    });

    test('empty input → empty output for every filter', () {
      for (final f in TaskOwnerFilter.values) {
        expect(filterByOwner(const [], f), isEmpty);
      }
    });
  });

  group('countAgentTasks', () {
    test('counts only agent-owned tasks', () {
      expect(
        countAgentTasks([
          _task('u1', owner: 'user'),
          _task('a1', owner: 'agent'),
          _task('a2', owner: 'agent'),
        ]),
        2,
      );
    });

    test('is zero when there are no agent tasks', () {
      expect(countAgentTasks([_task('u', owner: 'user')]), 0);
    });
  });

  group('TaskOwnerFilter labels', () {
    test('expose short chip labels', () {
      expect(TaskOwnerFilter.all.label, 'All');
      expect(TaskOwnerFilter.mine.label, 'Mine');
      expect(TaskOwnerFilter.ai.label, 'AI');
    });
  });
}
