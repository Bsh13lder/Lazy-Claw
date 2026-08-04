// The task detail sheet's BUDGET state machine, asserted directly.
//
// The widget tests (task_detail_budget_test.dart) prove these transitions are
// WIRED; this file proves they are CORRECT — in particular that `original`
// (the save path's three-way baseline) never moves, and that a committed
// top-up can't leave a stale rejection behind.

import 'package:flutter_test/flutter_test.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_budget_control.dart';
import 'package:lazyclaw_mobile/screens/tasks/task_budget_edit_state.dart';

void main() {
  test('a task with an allocation opens with the editor CLOSED', () {
    const state = TaskBudgetEditState.forTask(250);
    expect(state.original, 250);
    expect(state.draft, 250);
    expect(state.editor, TaskBudgetEditor.none);
    expect(state.error, isNull);
  });

  test('tapping the figure opens the allocation editor', () {
    final state = const TaskBudgetEditState.forTask(250).editing();
    expect(state.editor, TaskBudgetEditor.allocation);
    expect(state.draft, 250);
  });

  test('typing moves the draft but never the saved baseline', () {
    final state = const TaskBudgetEditState.forTask(250).editing().typed('400');
    expect(state.draft, 400);
    expect(state.original, 250);
    expect(state.error, isNull);
  });

  test('emptying the field drafts "no allocation" and stays valid', () {
    final state = const TaskBudgetEditState.forTask(250).editing().typed('');
    expect(state.draft, isNull);
    expect(state.error, isNull);
  });

  test('junk raises the inline error and keeps the last good draft', () {
    final state = const TaskBudgetEditState.forTask(250).editing().typed('-9');
    expect(state.draft, 250);
    expect(state.error, kTaskAllocationNegativeError);
  });

  test('a committed top-up closes the editor and clears any stale error', () {
    final state = const TaskBudgetEditState.forTask(300)
        .editing()
        .typed('-9')
        .toppingUp()
        .toppedUp(350);
    expect(state.draft, 350);
    expect(state.original, 300);
    expect(state.editor, TaskBudgetEditor.none);
    expect(state.error, isNull);
  });

  test('cancelling a top-up leaves the allocation alone', () {
    final state = const TaskBudgetEditState.forTask(300).toppingUp().closed();
    expect(state.editor, TaskBudgetEditor.none);
    expect(state.draft, 300);
  });

  test('a refused save re-opens the editor so the message is visible', () {
    final state = const TaskBudgetEditState.forTask(250)
        .closed()
        .rejected(kTaskAllocationInvalidError);
    expect(state.editor, TaskBudgetEditor.allocation);
    expect(state.error, kTaskAllocationInvalidError);
  });

  test('every transition returns a NEW value — nothing is mutated', () {
    const initial = TaskBudgetEditState.forTask(250);
    final edited = initial.editing().typed('400');
    expect(initial.draft, 250);
    expect(initial.editor, TaskBudgetEditor.none);
    expect(identical(initial, edited), isFalse);
  });
}
