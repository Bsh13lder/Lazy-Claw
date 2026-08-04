/// The task detail sheet's HAND-OFF actions and the pure helpers behind them.
///
/// Extracted out of `task_detail_sheet.dart` — every function here opens
/// another surface (a comment thread, the reschedule sheet, the add-link
/// dialog) or transforms a string, and none of them touches the sheet's own
/// editable state. They were the largest block in that file that had nothing
/// to do with editing a task, and the file sits on this project's 800-line
/// ceiling; auto-save had to be paid for by moving something out rather than
/// by growing past it.
///
/// This is a MOVE, not a rewrite: the rules and their comments are the ones
/// that were already there. The only shape change is that `_subtaskTitle`
/// became a pure function over an explicit list, which also makes it testable
/// without a widget tree.
library;

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../models/project.dart';
import '../../models/subtask.dart';
import '../../models/task.dart';
import '../../providers/tasks_provider.dart';
import '../expenses/add_expense_for_task.dart';
import 'add_link_dialog.dart';
import 'reschedule_sheet.dart';
import 'task_budget_control.dart';
import 'task_comments_section.dart';

/// The sub-task's title for a comments-sheet header, falling back to a generic
/// label if the id no longer matches (defensive only — the badge that opens
/// that sheet is only ever rendered for a live sub-task).
String subtaskTitleById(List<Subtask> subtasks, String id) => subtasks
    .firstWhere(
      (s) => s.id == id,
      orElse: () => const Subtask(id: '', title: 'Sub-task', done: false),
    )
    .title;

/// Parse a task's stored `tags` (a JSON-array string) into a list. Tolerant:
/// null / empty / malformed → `[]`.
List<String> parseTaskTags(String? raw) {
  if (raw == null || raw.trim().isEmpty) return [];
  try {
    final decoded = jsonDecode(raw);
    if (decoded is List) return decoded.map((e) => e.toString()).toList();
  } catch (_) {
    // A hand-edited or half-written value is not worth failing an open over.
  }
  return [];
}

/// Open the TASK-level comment thread (the same sheet a sub-task's 💬 badge
/// opens, just scoped to the task).
///
/// [live] is the freshly-watched task, not the sheet's opening snapshot:
/// comments write through the notifier immediately, so that snapshot goes
/// stale the moment one is added.
Future<void> openTaskCommentsSheet(
  BuildContext context,
  WidgetRef ref, {
  required Task live,
  required String taskId,
}) {
  return showCommentsSheet(
    context,
    title: 'Comments',
    comments: taskLevelComments(live.taskComments),
    onAdd: (text) => ref.read(tasksProvider.notifier).addComment(taskId, text),
    onDelete: (cid) =>
        ref.read(tasksProvider.notifier).deleteComment(taskId, cid),
    onAddLink: () => showAddLinkDialog(context),
  );
}

/// Open ONE sub-task's comment thread — the same sheet as
/// [openTaskCommentsSheet], scoped to [subtaskId] instead of to the task.
Future<void> openSubtaskCommentsSheet(
  BuildContext context,
  WidgetRef ref, {
  required Task live,
  required String taskId,
  required String subtaskId,
  required String title,
}) {
  return showCommentsSheet(
    context,
    title: title,
    comments: [
      for (final c in live.taskComments)
        if (c.subtaskId == subtaskId) c,
    ],
    onAdd: (text) => ref
        .read(tasksProvider.notifier)
        .addComment(taskId, text, subtaskId: subtaskId),
    onDelete: (cid) =>
        ref.read(tasksProvider.notifier).deleteComment(taskId, cid),
    onAddLink: () => showAddLinkDialog(context),
  );
}

/// Close the detail sheet and open the Smart Fast Reschedule sheet for [task].
///
/// The reschedule writes through [TasksNotifier.updateTask] itself, so this
/// just hands off. NOTE for auto-save: the caller must flush first — this pops
/// the detail sheet, and a quick reschedule is a deliberate "just move the
/// date" action that must not silently discard what the user typed above it.
Future<void> handOffToReschedule(
  BuildContext context,
  WidgetRef ref,
  Task task,
) async {
  final navigator = Navigator.of(context);
  // Use the navigator's (still-mounted) context to present the next sheet —
  // the caller's own context becomes defunct once we pop it below.
  final rootContext = navigator.context;
  navigator.pop();
  await showRescheduleSheet(rootContext, ref, task);
}

/// Open the task-scoped Add Expense sheet, optionally pinned to one sub-task.
///
/// [subtaskId] must name a SAVED sub-task — the affordance that reaches this is
/// hidden for in-sheet, not-yet-saved ones (see [SubtaskEditor]).
///
/// No explicit refresh afterwards: the detail sheet watches `budgetsProvider`,
/// and `addExpense` writes an optimistic row into that state, so the rollup and
/// the sub-task chips re-render on the same frame the sheet stays open for.
Future<void> openTaskExpenseSheet(
  BuildContext context,
  WidgetRef ref, {
  required Project? project,
  required String taskId,
  String? subtaskId,
  String? contextLabel,
}) async {
  // Explain, rather than silently do nothing, when the task has no
  // (resolvable) project. The sheet deliberately still SHOWS the affordance —
  // hiding it would make the feature look absent instead of blocked.
  if (project == null) {
    ScaffoldMessenger.maybeOf(
      context,
    )?.showSnackBar(const SnackBar(content: Text(kTaskBudgetNoProjectReason)));
    return;
  }
  await showAddExpenseForTaskSheet(
    context,
    ref,
    projectId: project.id,
    taskId: taskId,
    subtaskId: subtaskId,
    contextLabel: contextLabel,
  );
}

/// Splice a `[text](url)` markdown link into [controller] at the cursor.
///
/// Falls back to appending at the end when the selection is invalid (e.g. the
/// field hasn't been focused yet, so the controller's selection is still the
/// default collapsed-at--1). Returns false when the dialog was dismissed, so
/// the caller can skip a pointless rebuild.
Future<bool> insertLinkIntoNotes(
  BuildContext context,
  TextEditingController controller,
) async {
  final result = await showAddLinkDialog(context);
  if (result == null || !context.mounted) return false;
  final text = controller.text;
  final selection = controller.selection;
  final start = selection.isValid ? selection.start : text.length;
  final end = selection.isValid ? selection.end : text.length;
  controller.value = TextEditingValue(
    text: text.replaceRange(start, end, result),
    selection: TextSelection.collapsed(offset: start + result.length),
  );
  return true;
}
