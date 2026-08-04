/// The task detail sheet's BUDGET control: a tappable allocated-vs-spent
/// readout, a compact money dropdown for the two ACTIONS, and whichever inline
/// editor those open.
///
/// WHY it replaced a bare "allocated budget" text field: the field only ever
/// recorded an INTENTION. There was no way to record what the task actually
/// cost, so the sheet could show "€250 allocated" next to a task that had
/// already burned €400 — the number the user cares about was the one the UI
/// couldn't show. The dropdown keeps the allocation (unchanged save
/// semantics — it still writes through the sheet's own `_budgetController`)
/// and adds the second half: "Add expense", scoped to this task.
///
/// WHY the readout then became the control (2026-08-03): showing "Allocated
/// $300" as dead text and hiding the way to change it behind a dropdown item
/// that revealed a field elsewhere gave the same number two homes. The figure
/// is now the affordance — one tap opens the editor in place, seeded — and the
/// dropdown carries only what a tap on a number cannot express: TOP UP (add to
/// the allocation) and ADD EXPENSE (record real spend). There is deliberately
/// no second path to the allocation field.
///
/// Extracted into its own file rather than grown into `task_detail_sheet.dart`,
/// which was already past this project's 800-line ceiling. The pure money math
/// lives in `task_budget_math.dart` and the top-up form in
/// `task_budget_topup.dart`; both are re-exported here so existing
/// `import '.../task_budget_control.dart'` call sites keep resolving.
library;

import 'package:flutter/material.dart';

import '../../ui/ui.dart';
import 'task_budget_math.dart';
import 'task_budget_topup.dart';
import 'task_section_label.dart';

export 'task_budget_math.dart';
export 'task_budget_topup.dart';

/// What the money dropdown can do. The allocation itself is NOT here — it is
/// edited by tapping the figure (see the library doc).
enum TaskBudgetAction {
  /// Open the top-up form: `allocated += X`.
  topUp,

  /// Open the task-scoped Add Expense sheet.
  addExpense,
}

/// Which inline editor the BUDGET block is currently showing. Exactly one at a
/// time: two money fields open at once is how a user tops up the wrong number.
enum TaskBudgetEditor { none, allocation, topUp }

/// Stable keys, shared with the tests so a rename can't silently orphan them.
const Key kTaskBudgetMenuKey = Key('task-detail-budget-menu');
const Key kTaskBudgetTopUpItemKey = Key('task-detail-budget-menu-topup');
const Key kTaskBudgetExpenseItemKey = Key('task-detail-budget-menu-expense');
const Key kTaskBudgetSummaryKey = Key('task-detail-budget-summary');
const Key kTaskBudgetSummaryTapKey = Key('task-detail-budget-summary-tap');

/// The allocated-budget field's key — the editor a readout tap opens.
const Key kTaskBudgetFieldKey = Key('task-detail-budget');

/// Why "Add expense" is unavailable. Shown UNDER the disabled row rather than
/// only discovered at submit time — an expense has to land in a project, and
/// the task's project is the only one it may land in.
const String kTaskBudgetNoProjectReason = 'Pick a project first';

/// Glyph size for this control's inline icons. Named so the readout's edit
/// pencil and the dropdown rows can't drift apart.
const double _kGlyph = 16;

// ── The section ──────────────────────────────────────────────────────────────

/// The whole BUDGET block: the header, the money dropdown + tappable readout,
/// and whichever inline editor is open ([TaskBudgetEditor]).
///
/// The editors live HERE rather than back in the sheet because they are the
/// readout's / dropdown's own disclosure targets — keeping them apart meant a
/// reader of either half had to go find the other to know when a field appears.
/// The parent still owns the allocation controller (Save reads it) and the
/// [editor] flag.
class TaskBudgetSection extends StatelessWidget {
  const TaskBudgetSection({
    super.key,
    required this.allocated,
    required this.spent,
    required this.currency,
    required this.editor,
    required this.onEditAllocated,
    required this.onTopUp,
    required this.onTopUpCommitted,
    required this.onCancelTopUp,
    required this.onAddExpense,
    required this.canAddExpense,
    required this.allocatedController,
    required this.allocatedFocusNode,
    required this.onAllocatedChanged,
    this.allocatedError,
  });

  /// The allocation AS IT CURRENTLY STANDS IN THE SHEET (the working draft,
  /// not the saved value) — so an in-progress edit or a committed top-up shows
  /// in the readout immediately.
  final double? allocated;

  final double spent;
  final String currency;

  /// Which inline editor is open.
  final TaskBudgetEditor editor;

  /// Open the allocation editor (tapping the figure).
  final VoidCallback onEditAllocated;

  /// Open the top-up form.
  final VoidCallback onTopUp;

  /// A validated top-up: carries the NEW TOTAL, not the delta.
  final ValueChanged<double> onTopUpCommitted;

  final VoidCallback onCancelTopUp;
  final VoidCallback onAddExpense;
  final bool canAddExpense;

  final TextEditingController allocatedController;
  final FocusNode allocatedFocusNode;

  /// Fired on every keystroke in the allocation field so the parent can keep
  /// the readout (and its own validation) in step with what is typed.
  final ValueChanged<String> onAllocatedChanged;

  /// Inline rejection for the allocation field, or null when it is fine.
  final String? allocatedError;

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
          onTopUp: onTopUp,
          onAddExpense: onAddExpense,
        ),
        if (editor == TaskBudgetEditor.allocation) ...[
          const SizedBox(height: AppSpacing.sm),
          LzTextField(
            controller: allocatedController,
            fieldKey: kTaskBudgetFieldKey,
            focusNode: allocatedFocusNode,
            hint: 'Allocated budget (empty = none)',
            prefixIcon: Icons.account_balance_wallet_outlined,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            textInputAction: TextInputAction.done,
            errorText: allocatedError,
            onChanged: onAllocatedChanged,
          ),
        ],
        if (editor == TaskBudgetEditor.topUp) ...[
          const SizedBox(height: AppSpacing.sm),
          TaskTopUpEditor(
            allocated: allocated,
            currency: currency,
            onCommit: onTopUpCommitted,
            onCancel: onCancelTopUp,
          ),
        ],
      ],
    );
  }
}

/// The dropdown + tappable readout row. Purely presentational: it owns no money
/// state, renders what it is handed, and reports the chosen action upward.
class TaskBudgetControl extends StatelessWidget {
  const TaskBudgetControl({
    super.key,
    required this.allocated,
    required this.spent,
    required this.currency,
    required this.onEditAllocated,
    required this.onTopUp,
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

  /// Tapping the figure edits it in place.
  final VoidCallback onEditAllocated;

  final VoidCallback onTopUp;
  final VoidCallback onAddExpense;

  /// False greys out "Add expense" and surfaces [disabledReason] beneath it.
  final bool canAddExpense;
  final String disabledReason;

  @override
  Widget build(BuildContext context) {
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
            TaskBudgetAction.topUp => onTopUp(),
            TaskBudgetAction.addExpense => onAddExpense(),
          },
          itemBuilder: (_) => [
            PopupMenuItem<TaskBudgetAction>(
              key: kTaskBudgetTopUpItemKey,
              value: TaskBudgetAction.topUp,
              height: AppSpacing.xxxl,
              child: const _MenuRow(
                icon: Icons.savings_outlined,
                label: 'Top up',
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
        const SizedBox(width: AppSpacing.sm),
        Expanded(
          child: _AllocatedReadout(
            allocated: allocated,
            spent: spent,
            currency: currency,
            onTap: onEditAllocated,
          ),
        ),
      ],
    );
  }
}

/// The allocated-vs-spent line — and the way to change the allocation.
///
/// Rendered as a tap target with a trailing pencil so the figure reads as
/// editable; the overspent treatment (red + bold) is unchanged, and applies to
/// the text itself so it survives whatever wrapper this sits in.
class _AllocatedReadout extends StatelessWidget {
  const _AllocatedReadout({
    required this.allocated,
    required this.spent,
    required this.currency,
    required this.onTap,
  });

  final double? allocated;
  final double spent;
  final String currency;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final over = taskBudgetOverspent(allocated, spent);
    return InkWell(
      key: kTaskBudgetSummaryTapKey,
      onTap: onTap,
      borderRadius: AppRadii.rMd,
      child: Padding(
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.sm,
          vertical: AppSpacing.xs + 2,
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Flexible(
              child: Text(
                taskBudgetSummaryLabel(allocated, spent, currency),
                key: kTaskBudgetSummaryKey,
                style: AppText.caption.copyWith(
                  color: over ? AppColors.error : AppColors.textSecondary,
                  fontWeight: over ? FontWeight.w700 : FontWeight.w500,
                ),
              ),
            ),
            const SizedBox(width: AppSpacing.xs),
            const Icon(
              Icons.edit_outlined,
              size: _kGlyph,
              color: AppColors.textMuted,
            ),
          ],
        ),
      ),
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
          const Icon(Icons.expand_more, size: _kGlyph, color: AppColors.textMuted),
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
          size: _kGlyph,
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
