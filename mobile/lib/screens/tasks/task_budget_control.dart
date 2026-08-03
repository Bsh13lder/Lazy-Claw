/// The task detail sheet's BUDGET control: one compact money dropdown plus an
/// allocated-vs-spent readout.
///
/// WHY it replaced a bare "allocated budget" text field: the field only ever
/// recorded an INTENTION. There was no way to record what the task actually
/// cost, so the sheet could show "€250 allocated" next to a task that had
/// already burned €400 — the number the user cares about was the one the UI
/// couldn't show. The dropdown keeps the allocation (unchanged save
/// semantics — it still writes through the sheet's own `_budgetController`)
/// and adds the second half: "Add expense", scoped to this task.
///
/// Extracted into its own file rather than grown into `task_detail_sheet.dart`,
/// which was already past this project's 800-line ceiling.
library;

import 'package:flutter/material.dart';

import '../../ui/ui.dart';
import '../expenses/money_helpers.dart' show fmtMoney;
import 'task_section_label.dart';

/// What the money dropdown can do.
enum TaskBudgetAction {
  /// Reveal + focus the allocated-budget field (the pre-existing behavior).
  allocated,

  /// Open the task-scoped Add Expense sheet.
  addExpense,
}

/// Stable keys, shared with the tests so a rename can't silently orphan them.
const Key kTaskBudgetMenuKey = Key('task-detail-budget-menu');
const Key kTaskBudgetAllocatedItemKey = Key(
  'task-detail-budget-menu-allocated',
);
const Key kTaskBudgetExpenseItemKey = Key('task-detail-budget-menu-expense');
const Key kTaskBudgetSummaryKey = Key('task-detail-budget-summary');

/// Why "Add expense" is unavailable. Shown UNDER the disabled row rather than
/// only discovered at submit time — an expense has to land in a project, and
/// the task's project is the only one it may land in.
const String kTaskBudgetNoProjectReason = 'Pick a project first';

// ── Pure label helpers (unit-tested; no widgets involved) ─────────────────────

/// The readout beside the dropdown.
///
/// Both figures are shown together on purpose: an allocation without a spend
/// is a promise, and a spend without its allocation is a number with no
/// meaning. Nothing at all reads as an invitation rather than a broken "0".
String taskBudgetSummaryLabel(
  double? allocated,
  double spent,
  String currency,
) {
  if (allocated == null && spent == 0) return 'No budget yet';
  if (allocated == null) return 'Spent ${fmtMoney(currency, spent)}';
  return 'Allocated ${fmtMoney(currency, allocated)} · '
      'Spent ${fmtMoney(currency, spent)}';
}

/// Whether [spent] has passed [allocated]. Strictly greater — landing exactly
/// on the allocation is on budget, not over it. Always false without an
/// allocation: there is no line to cross.
bool taskBudgetOverspent(double? allocated, double spent) =>
    allocated != null && spent > allocated;

// ── The section ──────────────────────────────────────────────────────────────

/// The whole BUDGET block: the header, the money dropdown + readout, and the
/// allocated-budget field the dropdown reveals.
///
/// The field lives HERE rather than back in the sheet because it is the
/// dropdown's own disclosure target — keeping them apart meant a reader of
/// either half had to go find the other to know when the field appears.
/// The parent still owns the controller (Save reads it) and the
/// [showAllocatedField] flag.
class TaskBudgetSection extends StatelessWidget {
  const TaskBudgetSection({
    super.key,
    required this.allocated,
    required this.spent,
    required this.currency,
    required this.onEditAllocated,
    required this.onAddExpense,
    required this.canAddExpense,
    required this.showAllocatedField,
    required this.allocatedController,
    required this.allocatedFocusNode,
  });

  final double? allocated;
  final double spent;
  final String currency;
  final VoidCallback onEditAllocated;
  final VoidCallback onAddExpense;
  final bool canAddExpense;

  /// Whether the disclosure target is on screen.
  final bool showAllocatedField;

  final TextEditingController allocatedController;
  final FocusNode allocatedFocusNode;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        TaskSectionLabel('BUDGET'),
        const SizedBox(height: AppSpacing.sm),
        TaskBudgetControl(
          allocated: allocated,
          spent: spent,
          currency: currency,
          canAddExpense: canAddExpense,
          onEditAllocated: onEditAllocated,
          onAddExpense: onAddExpense,
        ),
        if (showAllocatedField) ...[
          const SizedBox(height: AppSpacing.sm),
          LzTextField(
            controller: allocatedController,
            fieldKey: const Key('task-detail-budget'),
            focusNode: allocatedFocusNode,
            hint: 'Allocated budget (optional)',
            prefixIcon: Icons.account_balance_wallet_outlined,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            textInputAction: TextInputAction.done,
          ),
        ],
      ],
    );
  }
}

/// The dropdown + readout row. Purely presentational: it owns no money state,
/// renders what it is handed, and reports the chosen action upward.
class TaskBudgetControl extends StatelessWidget {
  const TaskBudgetControl({
    super.key,
    required this.allocated,
    required this.spent,
    required this.currency,
    required this.onEditAllocated,
    required this.onAddExpense,
    this.canAddExpense = true,
    this.disabledReason = kTaskBudgetNoProjectReason,
  });

  /// The task's allocated budget, or null when it has none.
  final double? allocated;

  /// Live (non-void) money already recorded against the task, INCLUDING
  /// sub-task-pinned expenses — see `taskExpenseTotal`.
  final double spent;

  /// The currency both figures render in.
  final String currency;

  final VoidCallback onEditAllocated;
  final VoidCallback onAddExpense;

  /// False greys out "Add expense" and surfaces [disabledReason] beneath it.
  final bool canAddExpense;
  final String disabledReason;

  @override
  Widget build(BuildContext context) {
    final over = taskBudgetOverspent(allocated, spent);
    return Row(
      children: [
        PopupMenuButton<TaskBudgetAction>(
          key: kTaskBudgetMenuKey,
          tooltip: 'Budget actions',
          padding: EdgeInsets.zero,
          color: AppColors.bgSurfaceElevated,
          shape: const RoundedRectangleBorder(borderRadius: AppRadii.rLg),
          position: PopupMenuPosition.under,
          onSelected: (action) => switch (action) {
            TaskBudgetAction.allocated => onEditAllocated(),
            TaskBudgetAction.addExpense => onAddExpense(),
          },
          itemBuilder: (_) => [
            PopupMenuItem<TaskBudgetAction>(
              key: kTaskBudgetAllocatedItemKey,
              value: TaskBudgetAction.allocated,
              height: AppSpacing.xxxl,
              child: const _MenuRow(
                icon: Icons.savings_outlined,
                label: 'Allocated budget',
              ),
            ),
            PopupMenuItem<TaskBudgetAction>(
              key: kTaskBudgetExpenseItemKey,
              value: TaskBudgetAction.addExpense,
              enabled: canAddExpense,
              // A two-line row needs the taller slot; keeping both at the
              // short height clips the reason text.
              height: canAddExpense
                  ? AppSpacing.xxxl
                  : AppSpacing.xxxl + AppSpacing.lg,
              child: _MenuRow(
                icon: Icons.add_card_outlined,
                label: 'Add expense',
                reason: canAddExpense ? null : disabledReason,
              ),
            ),
          ],
          child: const _BudgetTrigger(),
        ),
        const SizedBox(width: AppSpacing.md),
        Expanded(
          child: Text(
            taskBudgetSummaryLabel(allocated, spent, currency),
            key: kTaskBudgetSummaryKey,
            style: AppText.caption.copyWith(
              color: over ? AppColors.error : AppColors.textSecondary,
              fontWeight: over ? FontWeight.w700 : FontWeight.w500,
            ),
          ),
        ),
      ],
    );
  }
}

/// The dropdown's visible affordance — deliberately the same pill shape as
/// `ProjectChip` so the sheet's controls read as one family.
class _BudgetTrigger extends StatelessWidget {
  const _BudgetTrigger();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.xs + 2,
      ),
      decoration: BoxDecoration(
        color: AppColors.bgSurfaceElevated,
        borderRadius: AppRadii.rPill,
        border: Border.all(color: AppColors.borderDefault),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(
            Icons.attach_money_rounded,
            size: 15,
            color: AppColors.accent,
          ),
          const SizedBox(width: AppSpacing.xs),
          Text(
            'Budget',
            style: AppText.caption.copyWith(
              color: AppColors.textPrimary,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          const Icon(Icons.expand_more, size: 16, color: AppColors.textMuted),
        ],
      ),
    );
  }
}

/// One dropdown row: glyph + label, with an optional muted second line
/// explaining why the row is unavailable.
class _MenuRow extends StatelessWidget {
  const _MenuRow({required this.icon, required this.label, this.reason});

  final IconData icon;
  final String label;
  final String? reason;

  @override
  Widget build(BuildContext context) {
    final disabled = reason != null;
    final fg = disabled ? AppColors.textMuted : AppColors.textPrimary;
    return Row(
      children: [
        Icon(
          icon,
          size: 16,
          color: disabled ? AppColors.textMuted : AppColors.accent,
        ),
        const SizedBox(width: AppSpacing.sm),
        Flexible(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                label,
                style: AppText.body.copyWith(
                  color: fg,
                  fontWeight: FontWeight.w600,
                ),
              ),
              if (reason != null)
                Text(
                  reason!,
                  style: AppText.caption.copyWith(color: AppColors.textMuted),
                ),
            ],
          ),
        ),
      ],
    );
  }
}
