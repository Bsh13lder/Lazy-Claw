// Unit tests for the pure task/sub-task expense rollup family.
//
// These feed two very different affordances and must NOT be conflated:
//   * `taskExpenseTotal`      → the "spent" figure beside the task's allocated
//                               budget (ALL live money on the task, including
//                               money pinned to one of its sub-tasks).
//   * `subtaskExpenseTotals`  → the per-row money chip in `SubtaskEditor`
//                               (only money pinned to that exact sub-task).
//
// Pure functions, no widgets — so no FakeAsync/sqflite hazard here.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/expense.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_expense_rollup.dart';

Expense _e({
  required String id,
  String? taskId,
  String? subtaskId,
  double amount = 10.0,
  String currency = 'USD',
  String status = 'posted',
}) => Expense(
  id: id,
  projectId: 'p1',
  taskId: taskId,
  subtaskId: subtaskId,
  amount: amount,
  currency: currency,
  description: 'x',
  status: status,
);

void main() {
  group('taskExpenseTotal', () {
    test('is 0 for an empty ledger', () {
      expect(taskExpenseTotal(const [], 't1'), 0);
    });

    test('sums every live expense on the task', () {
      final total = taskExpenseTotal([
        _e(id: 'a', taskId: 't1', amount: 12.5),
        _e(id: 'b', taskId: 't1', amount: 7.5),
      ], 't1');
      expect(total, 20.0);
    });

    test(
      'INCLUDES sub-task-pinned money — spending on a sub-task is spending '
      'on the task (this is what makes it differ from subtaskExpenseTotals)',
      () {
        final total = taskExpenseTotal([
          _e(id: 'a', taskId: 't1', amount: 10),
          _e(id: 'b', taskId: 't1', subtaskId: 's1', amount: 30),
        ], 't1');
        expect(total, 40.0);
      },
    );

    test('excludes void expenses', () {
      final total = taskExpenseTotal([
        _e(id: 'a', taskId: 't1', amount: 10),
        _e(id: 'void', taskId: 't1', amount: 999, status: 'void'),
      ], 't1');
      expect(total, 10.0);
    });

    test('excludes another task\'s expenses and task-less ones', () {
      final total = taskExpenseTotal([
        _e(id: 'a', taskId: 't1', amount: 10),
        _e(id: 'b', taskId: 't2', amount: 50),
        _e(id: 'c', amount: 70),
      ], 't1');
      expect(total, 10.0);
    });
  });

  group('taskExpenseCurrency', () {
    test('defaults to USD when the task has no live expense', () {
      expect(taskExpenseCurrency(const [], 't1'), 'USD');
      expect(
        taskExpenseCurrency([
          _e(id: 'v', taskId: 't1', currency: 'EUR', status: 'void'),
        ], 't1'),
        'USD',
      );
    });

    test('takes the first live task-linked expense\'s currency', () {
      expect(
        taskExpenseCurrency([
          _e(id: 'other', taskId: 't2', currency: 'GBP'),
          _e(id: 'a', taskId: 't1', currency: 'EUR'),
        ], 't1'),
        'EUR',
      );
    });

    test('sees a sub-task-pinned expense too (task-level scope)', () {
      expect(
        taskExpenseCurrency([
          _e(id: 'a', taskId: 't1', subtaskId: 's1', currency: 'EUR'),
        ], 't1'),
        'EUR',
      );
    });
  });

  // These two moved here verbatim from task_detail_sheet.dart; re-tested at
  // their new home so the extraction is covered on its own terms.
  group('subtaskExpenseTotals (moved, unchanged behavior)', () {
    test('keys only by sub-task, ignoring task-level money', () {
      final totals = subtaskExpenseTotals([
        _e(id: 'a', taskId: 't1', subtaskId: 's1', amount: 10),
        _e(id: 'b', taskId: 't1', subtaskId: 's1', amount: 5),
        _e(id: 'c', taskId: 't1', amount: 999),
      ], 't1');
      expect(totals, {'s1': 15.0});
    });

    test('drops void rows and other tasks', () {
      final totals = subtaskExpenseTotals([
        _e(id: 'v', taskId: 't1', subtaskId: 's1', amount: 9, status: 'void'),
        _e(id: 'o', taskId: 't2', subtaskId: 's1', amount: 9),
      ], 't1');
      expect(totals, isEmpty);
    });
  });

  group('subtaskExpenseCurrency (moved, unchanged behavior)', () {
    test('defaults to USD', () {
      expect(subtaskExpenseCurrency(const [], 't1'), 'USD');
    });

    test('ignores a task-level expense when picking the currency', () {
      expect(
        subtaskExpenseCurrency([
          _e(id: 'task-level', taskId: 't1', currency: 'GBP'),
          _e(id: 'pinned', taskId: 't1', subtaskId: 's1', currency: 'EUR'),
        ], 't1'),
        'EUR',
      );
    });
  });

  // Moved out of the detail sheet's `_resolveProject` (the sheet was at its
  // file-size ceiling); the rules are the ones that were already there.
  group('resolveTaskProject (moved, unchanged behavior)', () {
    Project p(String id, String name) => Project(
      id: id,
      name: name,
      budget: 0,
      currency: 'USD',
      status: 'active',
    );

    test('a blank / absent category resolves to no project', () {
      expect(
        resolveTaskProject(name: null, preferred: [p('p1', 'Home')], fallback: const []),
        isNull,
      );
      expect(
        resolveTaskProject(name: '  ', preferred: [p('p1', 'Home')], fallback: const []),
        isNull,
      );
    });

    test('the picker list wins when the caller supplied one', () {
      final match = resolveTaskProject(
        name: 'Home',
        preferred: [p('picker', 'Home')],
        fallback: [p('cache', 'Home')],
      );
      expect(match?.id, 'picker');
    });

    test(
      'falls back to the budgets cache — call sites that open the sheet with '
      'no picker projects still have a real project on the task',
      () {
        final match = resolveTaskProject(
          name: 'Home',
          preferred: const [],
          fallback: [p('cache', 'Home')],
        );
        expect(match?.id, 'cache');
      },
    );

    test('an unmatched name resolves to no project rather than guessing', () {
      expect(
        resolveTaskProject(
          name: 'Nowhere',
          preferred: [p('p1', 'Home')],
          fallback: const [],
        ),
        isNull,
      );
    });
  });
}
