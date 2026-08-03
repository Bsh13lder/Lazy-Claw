/// The task detail sheet's Save payload, derived purely.
///
/// Extracted out of `task_detail_sheet.dart` unchanged. WHY it earns its own
/// file (beyond that sheet being past the 800-line ceiling): every field here
/// carries a three-way rule — `null` = leave the column alone, `''` = force
/// clear it, anything else = set it — and getting one of them wrong silently
/// deletes user data on an unrelated edit. Sitting inside a `State` method it
/// could only ever be tested by driving a whole widget tree; as a pure
/// function each rule is asserted directly.
///
/// This is a MOVE, not a rewrite: the rules and their comments are the ones
/// that were already there.
library;

import 'dart:convert';

/// The arguments a Save hands to `TasksNotifier.updateTask`.
class TaskDetailPatch {
  const TaskDetailPatch({
    required this.title,
    required this.description,
    required this.priority,
    required this.dueDate,
    required this.category,
    required this.steps,
    required this.reminderAt,
    required this.recurring,
    required this.recurUntil,
    required this.tags,
    required this.allocatedBudget,
    required this.clearAllocatedBudget,
  });

  final String title;
  final String description;
  final String priority;

  /// Always written. `''` is the clear sentinel — a null would be read as
  /// "field untouched" and would silently no-op removing the due date.
  final String dueDate;

  final String? category;
  final String? steps;
  final String? reminderAt;
  final String? recurring;
  final String? recurUntil;
  final String? tags;
  final double? allocatedBudget;
  final bool clearAllocatedBudget;
}

/// Build the Save payload from the sheet's working state.
///
/// [reminderArg] is passed in already-derived: it depends on the reminder
/// preservation rules that live with the rest of the due/reminder state on
/// the sheet (see `_reminderArg` there), and splitting them across two files
/// is how they would drift.
TaskDetailPatch buildTaskDetailPatch({
  required String title,
  required String description,
  required String priority,
  required String? composedDue,
  required List<String> tags,
  required String originalTagsJson,
  required String budgetText,
  required double? originalBudget,
  required String? nextSteps,
  required String? originalSteps,
  required bool categoryTouched,
  required String? category,
  required bool recurrenceTouched,
  required String? nextCron,
  required bool recurUntilTouched,
  required String? recurUntil,
  required String? reminderArg,
}) {
  // Only write `tags` when they changed. Serialized as the JSON-array string
  // the DAO/cache carry; task_sync decodes it to a list for the PATCH. An
  // empty list ('[]') is a deliberate clear (differs from the on-open snapshot
  // only when the task actually had tags).
  final nextTagsJson = jsonEncode(tags);
  final tagsArg = nextTagsJson == originalTagsJson ? null : nextTagsJson;

  // Budget: empty field clears a previously-set budget; a parsed number that
  // differs sets it; otherwise leave untouched.
  final trimmedBudget = budgetText.trim();
  final parsedBudget = trimmedBudget.isEmpty
      ? null
      : double.tryParse(trimmedBudget);
  double? budgetArg;
  var clearBudget = false;
  if (trimmedBudget.isEmpty && originalBudget != null) {
    clearBudget = true;
  } else if (parsedBudget != null && parsedBudget != originalBudget) {
    budgetArg = parsedBudget;
  }

  // Only write `steps` when the checklist changed. An empty string (not null)
  // force-clears the column when every sub-task was removed.
  final stepsArg = nextSteps == originalSteps ? null : (nextSteps ?? '');

  // Only write `category` when the user touched the project. An empty string
  // (not null) force-clears the column when "No project" was chosen.
  final categoryArg = !categoryTouched ? null : (category ?? '');

  // Only write `recurring` when the user touched the repeat picker, so an
  // untouched custom/known cron is preserved. A "does not repeat" selection
  // sends an empty string to force-clear the column; otherwise the computed
  // cron (anchored to the due date) is sent.
  final recurringArg = !recurrenceTouched ? null : (nextCron ?? '');

  // Only write `recur_until` when the user touched the Ends control (the ''
  // sentinel clears back to "Never" — mirrors the recurring convention). When
  // the recurrence itself was just cleared, an end date is an orphan: clear it
  // too (but only when there is something to clear, so a plain edit doesn't
  // churn the column).
  String? recurUntilArg;
  if (recurringArg != null && recurringArg.isEmpty) {
    recurUntilArg = (recurUntil != null || recurUntilTouched) ? '' : null;
  } else if (recurUntilTouched) {
    recurUntilArg = recurUntil ?? '';
  }

  return TaskDetailPatch(
    title: title,
    description: description,
    priority: priority,
    // Send the `''` clear sentinel (not null) when the due date was removed,
    // so the clear reaches the cache + outbox and syncs.
    dueDate: composedDue ?? '',
    category: categoryArg,
    steps: stepsArg,
    reminderAt: reminderArg,
    recurring: recurringArg,
    recurUntil: recurUntilArg,
    tags: tagsArg,
    allocatedBudget: budgetArg,
    clearAllocatedBudget: clearBudget,
  );
}
