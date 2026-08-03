/// The data [AddTaskSheet] hands back to whoever opened it.
///
/// Extracted from `add_task_sheet.dart` (where it lived as a private class
/// mirrored by a 9-field anonymous record on `showAddTaskSheet`) so the submit
/// pipeline in `add_task_submit.dart` — which now has to consume it OUTSIDE
/// the sheet, to link an expense to the freshly-created task id — can name the
/// type instead of threading a positional record shape through three files.
/// Field access is unchanged for every existing caller.
library;

/// Immutable — the sheet builds one at submit time and never touches it again.
class AddTaskResult {
  const AddTaskResult({
    required this.title,
    required this.priority,
    this.dueDate,
    this.category,
    this.reminderAt,
    this.recurring,
    this.recurUntil,
    this.description,
    this.steps,
    this.expenseAmount,
  });

  final String title;
  final String priority;
  final String? dueDate;

  /// The project NAME (not id) this task belongs to, or null. Resolved to a
  /// real project id downstream — see `add_task_submit.dart`.
  final String? category;

  /// Absolute reminder instant (`due − lead`), or null for no reminder.
  final String? reminderAt;

  /// A standard 5-field cron expression when the task repeats, else null.
  final String? recurring;

  /// The series' end day (`yyyy-MM-dd`) when the task repeats until a date,
  /// else null (repeats forever / does not repeat).
  final String? recurUntil;

  /// Free-form notes → the task's `description`, or null when blank.
  final String? description;

  /// The serialized `[{id,title,done}]` sub-task checklist JSON, or null for
  /// an empty list.
  final String? steps;

  /// The amount to file as a LINKED expense alongside this task, or null for
  /// "no expense".
  ///
  /// Non-null ONLY when the user's expense confirmation chip was armed at
  /// submit time. This is a deliberate one-way gate: the sheet decides, the
  /// pipeline obeys. A null here can never become a money row downstream, so
  /// no amount of confusion in the submit path can spend money the user did
  /// not confirm.
  ///
  /// Note there is no `expenseCurrency`: the expense inherits its project's
  /// currency, exactly like the manual Add Expense flow (see
  /// `BudgetsNotifier.addExpense`). A typed `eur`/`$` marker is an ARMING
  /// signal only — honouring it here would introduce a currency divergence
  /// that the form-based path deliberately does not have.
  final double? expenseAmount;
}
