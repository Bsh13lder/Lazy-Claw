import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../providers/budgets_provider.dart';
import '../../ui/ui.dart';
import 'add_expense_sheet.dart';

/// Opens the Add Expense sheet already SCOPED to a task (and optionally one of
/// its sub-tasks), and reports whether the expense was created.
///
/// This is the entry point the task detail sheet uses for "add an expense on
/// this task / sub-task". It is the same [AddExpenseSheet] the Money tab
/// shows — one widget with a mode, not a second sheet — just with the project
/// pinned and the task link threaded into
/// [BudgetsNotifier.addExpense].
///
/// [projectId] MUST be the task's own project: the task determines where the
/// expense lands, and letting the user re-file it elsewhere would silently
/// desync the project budget rollup from the per-task/per-sub-task totals.
/// That is why the sheet renders it read-only in this mode
/// (`lockedProjectId`).
///
/// [contextLabel] is shown as a header pill (e.g. `Sub-task: Buy paint`) so a
/// scoped add is never mistaken for a plain project expense.
///
/// Returns true only when the create actually succeeded. Dismissing the sheet
/// — or a failed write, which leaves the sheet open with
/// `BudgetsState.error` set — returns false.
///
/// NOTE: passing a [subtaskId] without a [taskId] is rejected downstream
/// (`BudgetsDao.applyLocalExpenseCreate` throws, because the server rejects
/// that shape with a 400 and the row could never sync). [taskId] is required
/// here so that combination is unreachable from this call site.
Future<bool> showAddExpenseForTaskSheet(
  BuildContext context,
  WidgetRef ref, {
  required String projectId,
  required String taskId,
  String? subtaskId,
  String? contextLabel,
}) async {
  final projects = ref.read(budgetsProvider).projects;

  final created = await LzBottomSheet.show<bool>(
    context,
    title: 'Add Expense',
    builder: (_) => AddExpenseSheet(
      projects: projects,
      initialProjectId: projectId,
      lockedProjectId: projectId,
      contextLabel: contextLabel,
      onSubmit: (pid, amount, description, vendor) =>
          ref.read(budgetsProvider.notifier).addExpense(
                pid,
                amount,
                description,
                vendor: vendor,
                taskId: taskId,
                subtaskId: subtaskId,
              ),
    ),
  );

  // `show` resolves to null when the sheet is dismissed without submitting.
  return created ?? false;
}
