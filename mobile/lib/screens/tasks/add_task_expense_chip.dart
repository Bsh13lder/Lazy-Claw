/// The Add-Task sheet's expense confirmation chip: "＋ Add €40 expense".
///
/// Extracted from `add_task_sheet.dart` (already at this project's file-size
/// ceiling) rather than grown into it.
///
/// This is the ONE affordance standing between a recognized number in a task
/// title and a real money row, so it is deliberately loud and deliberately
/// dumb: it renders state and reports taps. Every decision about WHETHER an
/// amount exists lives in `core/smart_add_task_expense.dart`; the decision
/// about whether it is ARMED lives in [AddTaskExpenseArmState] below, which
/// the sheet owns. Nothing here can spend money on its own.
library;

import 'package:flutter/material.dart';

import '../../core/smart_add_task_expense.dart';
import '../../models/project.dart';
import '../../ui/ui.dart';
import '../expenses/money_helpers.dart' show fmtMoney;

/// Stable keys, shared with the tests so a rename can't silently orphan them.
const Key kAddTaskExpenseChipKey = Key('add-task-expense-chip');
const Key kAddTaskExpenseReasonKey = Key('add-task-expense-reason');

/// Why the chip can't be armed right now, shown next to the disabled chip.
const String kAddTaskExpenseNoProjectReason =
    'pick a project to attach an expense';

/// The sheet's sticky arm state for the expense chip.
///
/// Immutable, with [copyWith]-style transitions, mirroring this file family's
/// no-mutation rule. The `touched` flag is the same manual-override-wins
/// pattern the sheet already uses for `_categoryTouched` / `_dueDateTouched`
/// (and `add_expense_sheet.dart`'s `_amountTouched`): once the user has
/// spoken, a later re-parse must never overrule them — in EITHER direction.
/// Typing more words after deliberately switching the expense off must not
/// quietly switch it back on.
class AddTaskExpenseArmState {
  const AddTaskExpenseArmState({this.touched = false, this.manual = false});

  /// Whether the user has tapped the chip at all.
  final bool touched;

  /// The user's explicit choice; only meaningful while [touched].
  final bool manual;

  /// The arm state after a tap, given what it currently resolves to.
  AddTaskExpenseArmState toggled(bool currentlyArmed) =>
      AddTaskExpenseArmState(touched: true, manual: !currentlyArmed);

  /// Whether an expense should be filed for [match], given [hasProject].
  ///
  /// Default-armed ONLY on an explicit currency marker: a false-positive
  /// money row is far worse than a missed convenience tap, and a bare number
  /// ("buy 2 apples", "meeting at 3") is far too weak a signal to spend on.
  /// A missing project hard-disarms regardless of the user's choice — there
  /// is nowhere for the expense to land.
  bool isArmed(TaskExpenseMatch? match, {required bool hasProject}) {
    if (match == null || !hasProject) return false;
    return touched ? manual : match.hasCurrencyMarker;
  }
}

/// The confirmation chip. Renders nothing when [match] is null.
class AddTaskExpenseChip extends StatelessWidget {
  const AddTaskExpenseChip({
    super.key,
    required this.match,
    required this.armed,
    required this.project,
    required this.hasProject,
    required this.onToggle,
  });

  /// The detected amount, or null when the title carries none.
  final TaskExpenseMatch? match;

  /// Whether an expense will actually be filed on submit.
  final bool armed;

  /// The resolved project the expense would land in, when it already exists.
  /// Supplies the currency the amount is DISPLAYED in — see [_label].
  final Project? project;

  /// Whether the task has a project at all (it may name one that doesn't
  /// exist yet, in which case [project] is null but this is still true).
  final bool hasProject;

  final VoidCallback onToggle;

  @override
  Widget build(BuildContext context) {
    final m = match;
    if (m == null) return const SizedBox.shrink();

    return Padding(
      padding: const EdgeInsets.only(top: AppSpacing.sm),
      child: Row(
        children: [
          Flexible(
            child: LzChip(
              key: kAddTaskExpenseChipKey,
              label: _label(m),
              icon: armed
                  ? Icons.check_circle_outline
                  : Icons.add_circle_outline,
              selected: armed,
              // Money green — the same token `SmartAddController` paints an
              // amount span with, so the chip and the highlighted text in the
              // field read as one thing.
              color: hasProject ? AppColors.success : AppColors.textMuted,
              // A null onTap is what makes the chip non-interactive; the
              // reason text beside it explains why rather than leaving the
              // user to discover it at submit time.
              onTap: hasProject ? onToggle : null,
            ),
          ),
          if (!hasProject) ...[
            const SizedBox(width: AppSpacing.sm),
            Flexible(
              child: Text(
                kAddTaskExpenseNoProjectReason,
                key: kAddTaskExpenseReasonKey,
                style: AppText.caption.copyWith(color: AppColors.textMuted),
              ),
            ),
          ],
        ],
      ),
    );
  }

  /// `Add €40 expense`.
  ///
  /// The currency SHOWN is the destination project's, not the one the user
  /// typed, because that is the currency the row will actually be recorded in
  /// (`BudgetsNotifier.addExpense` inherits it from the project). Rendering a
  /// typed "€" over a USD project would promise something the write doesn't
  /// deliver. Falls back to the typed marker while the project doesn't exist
  /// yet (a `#newproject` token, created during submit), and to a bare number
  /// when there is neither.
  String _label(TaskExpenseMatch m) {
    final currency = project?.currency ?? m.currency;
    final amount = currency == null
        ? _plain(m.amount)
        : fmtMoney(currency, m.amount);
    return 'Add $amount expense';
  }

  /// Whole numbers without decimals, everything else with exactly two —
  /// matching `fmtMoney`'s own rule, minus the symbol.
  static String _plain(double v) => v == v.truncateToDouble()
      ? v.toInt().toString()
      : v.toStringAsFixed(2);
}
