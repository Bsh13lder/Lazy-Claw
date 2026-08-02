import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/subtask.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_sort.dart';

void main() {
  group('Task sorting utilities', () {
    test('sortDoneLast is a stable partition', () {
      Task t(String id, String status) => Task(
          id: id,
          userId: '',
          title: id,
          priority: 'medium',
          status: status,
          owner: 'user',
          nagCount: 0,
          createdAt: '2026-01-01');
      final input = [
        t('d1', 'done'),
        t('p1', 'todo'),
        t('d2', 'done'),
        t('p2', 'in_progress'),
        t('p3', 'todo')
      ];
      expect(sortDoneLast(input).map((x) => x.id).toList(),
          ['p1', 'p2', 'p3', 'd1', 'd2']);
      expect(input.map((x) => x.id).toList(),
          ['d1', 'p1', 'd2', 'p2', 'p3']); // input untouched (immutability)
    });

    test('sortSubtasksDoneLast partitions and preserves order', () {
      Subtask s(String id, bool done) => Subtask(id: id, title: id, done: done);
      final input = [s('a', true), s('b', false), s('c', true), s('d', false)];
      expect(sortSubtasksDoneLast(input).map((x) => x.id).toList(),
          ['b', 'd', 'a', 'c']);
      expect(input.map((x) => x.id).toList(),
          ['a', 'b', 'c', 'd']); // input untouched (immutability)
    });
  });
}
