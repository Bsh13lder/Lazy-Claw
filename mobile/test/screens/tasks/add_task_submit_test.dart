// Unit tests for the ADD-TASK submit pipeline — "create the task, then file
// the linked expense" — the step that actually spends the user's money.
//
// Deliberately plain `test()` (no `testWidgets`, no ProviderScope, no DB):
// `submitAddTaskResult` takes its four side effects as closures precisely so
// this ordering + failure-isolation contract can be pinned without a real
// sqflite handle. `testWidgets` runs inside FakeAsync, where a real DB call
// never completes and the test HANGS (see mobile/test/screens/ prior art).
//
// The contract under test:
//   * the task is created FIRST and is NEVER rolled back,
//   * the expense (when armed) carries the NEW task's id,
//   * an expense failure is reported to the caller, not swallowed,
//   * an un-armed chip files nothing at all.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/models/task.dart';
import 'package:lazyclaw_mobile/screens/tasks/add_task_result.dart';
import 'package:lazyclaw_mobile/screens/tasks/add_task_submit.dart';

Task _task(String id, String title) => Task(
  id: id,
  userId: 'u1',
  title: title,
  priority: 'medium',
  status: 'pending',
  owner: 'user',
  nagCount: 0,
  createdAt: '2026-08-03T10:00:00Z',
);

Project _project(String id, String name) =>
    Project(id: id, name: name, budget: 0, currency: 'EUR', status: 'active');

/// Records every side effect the pipeline fires, in order.
class _Recorder {
  final List<String> ensured = [];
  final List<String> createdTasks = [];
  final List<Map<String, Object?>> expenses = [];
  bool taskSucceeds = true;
  bool expenseSucceeds = true;
  List<Project> projects = const [];

  Future<AddTaskSubmitOutcome> run(AddTaskResult result) => submitAddTaskResult(
    result,
    ensureProject: (name) async => ensured.add(name),
    createTask: (r) async {
      createdTasks.add(r.title);
      return taskSucceeds ? _task('t-new', r.title) : null;
    },
    readProjects: () => projects,
    createLinkedExpense:
        ({
          required projectId,
          required amount,
          required description,
          required taskId,
        }) async {
          expenses.add({
            'projectId': projectId,
            'amount': amount,
            'description': description,
            'taskId': taskId,
          });
          return expenseSucceeds;
        },
  );
}

AddTaskResult _result({
  String title = 'buy paint',
  String? category,
  double? expenseAmount,
}) => AddTaskResult(
  title: title,
  priority: 'medium',
  category: category,
  expenseAmount: expenseAmount,
);

void main() {
  test('no expense armed → task only, zero expense writes', () async {
    final r = _Recorder()..projects = [_project('p1', 'home')];
    final outcome = await r.run(_result(category: 'home'));

    expect(outcome, AddTaskSubmitOutcome.taskOnly);
    expect(r.createdTasks, ['buy paint']);
    expect(r.expenses, isEmpty);
  });

  test('armed expense is created with the NEW task id', () async {
    final r = _Recorder()..projects = [_project('p1', 'home')];
    final outcome = await r.run(_result(category: 'home', expenseAmount: 40));

    expect(outcome, AddTaskSubmitOutcome.taskAndExpense);
    expect(r.ensured, ['home']);
    expect(r.createdTasks, ['buy paint']);
    expect(r.expenses, [
      {
        'projectId': 'p1',
        'amount': 40.0,
        'description': 'buy paint',
        'taskId': 't-new',
      },
    ]);
  });

  test('the project list is re-read AFTER ensureProject', () async {
    // A `#newproject` token is created by ensureProject during this very
    // submit, so resolving against a list captured beforehand would miss it
    // and drop the expense on the floor.
    final r = _Recorder();
    var ensuredYet = false;
    final outcome = await submitAddTaskResult(
      _result(category: 'newproject', expenseAmount: 12.5),
      ensureProject: (name) async {
        ensuredYet = true;
        r.ensured.add(name);
      },
      createTask: (res) async => _task('t-9', res.title),
      readProjects: () =>
          ensuredYet ? [_project('p-new', 'newproject')] : const [],
      createLinkedExpense:
          ({
            required projectId,
            required amount,
            required description,
            required taskId,
          }) async {
            r.expenses.add({'projectId': projectId, 'taskId': taskId});
            return true;
          },
    );

    expect(outcome, AddTaskSubmitOutcome.taskAndExpense);
    expect(r.expenses.single['projectId'], 'p-new');
  });

  test('expense failure leaves the task saved and is reported', () async {
    final r = _Recorder()
      ..projects = [_project('p1', 'home')]
      ..expenseSucceeds = false;
    final outcome = await r.run(_result(category: 'home', expenseAmount: 40));

    expect(outcome, AddTaskSubmitOutcome.expenseFailed);
    // The task must NOT be rolled back.
    expect(r.createdTasks, ['buy paint']);
    expect(r.expenses, hasLength(1));
  });

  test(
    'unresolvable project → task saved, expense reported as not filed',
    () async {
      // The chip is disabled without a project, so this is defense-in-depth:
      // an armed amount whose category resolves to nothing must never be
      // silently dropped.
      final r = _Recorder()..projects = const [];
      final outcome = await r.run(
        _result(category: 'ghost', expenseAmount: 40),
      );

      expect(outcome, AddTaskSubmitOutcome.expenseNoProject);
      expect(r.createdTasks, ['buy paint']);
      expect(r.expenses, isEmpty);
    },
  );

  test('task creation failure skips the expense entirely', () async {
    // Filing an unlinked expense for a task that does not exist would leave
    // an orphan money row the user never asked for.
    final r = _Recorder()
      ..projects = [_project('p1', 'home')]
      ..taskSucceeds = false;
    final outcome = await r.run(_result(category: 'home', expenseAmount: 40));

    expect(outcome, AddTaskSubmitOutcome.taskFailed);
    expect(r.expenses, isEmpty);
  });

  test('a blank category never calls ensureProject', () async {
    final r = _Recorder();
    await r.run(_result(category: '   '));
    expect(r.ensured, isEmpty);
  });

  test('outcome messages are user-facing and mention the failure', () {
    expect(AddTaskSubmitOutcome.taskOnly.expenseWarning, isNull);
    expect(AddTaskSubmitOutcome.taskAndExpense.expenseWarning, isNull);
    expect(AddTaskSubmitOutcome.taskFailed.expenseWarning, isNull);
    expect(
      AddTaskSubmitOutcome.expenseFailed.expenseWarning,
      contains('expense'),
    );
    expect(
      AddTaskSubmitOutcome.expenseNoProject.expenseWarning,
      contains('expense'),
    );
  });
}
