/// The task detail sheet's TOP UP editor: add an amount to the task's
/// allocation, previewing the new total before anything is committed.
///
/// SCOPE (repeated here because it is the thing most likely to be got wrong
/// later): this is NOT a credit ledger. A project top-up writes a
/// `budget_entries` row; a TASK top-up only moves `tasks.allocated_budget`,
/// the plaintext REAL column holding this task's slice of its project budget.
/// No schema change, no backend change, no new sync entity — the committed
/// total rides out through the sheet's existing allocated-budget save path
/// (see `task_detail_patch.dart`). Do not "upgrade" this into a ledger without
/// a real schema + sync design.
///
/// Lives beside — not inside — `task_budget_control.dart` so neither file grows
/// past this project's size guidance. It owns exactly its own two pieces of
/// state (the typed amount and the last rejection); the ALLOCATION itself stays
/// with the sheet, which is what Save reads.
library;

import 'package:flutter/material.dart';

import '../../ui/ui.dart';
import 'task_budget_math.dart';

/// Stable keys, shared with the tests so a rename can't silently orphan them.
const Key kTaskTopUpFieldKey = Key('task-detail-topup-amount');
const Key kTaskTopUpPreviewKey = Key('task-detail-topup-preview');
const Key kTaskTopUpSubmitKey = Key('task-detail-topup-submit');
const Key kTaskTopUpCancelKey = Key('task-detail-topup-cancel');

/// An inline "add to this task's allocation" form.
///
/// Commits through [onCommit] with the NEW TOTAL (already validated and rounded
/// to cents) rather than with the delta: the caller then has one number to
/// write and no chance of adding it twice.
class TaskTopUpEditor extends StatefulWidget {
  const TaskTopUpEditor({
    super.key,
    required this.allocated,
    required this.currency,
    required this.onCommit,
    required this.onCancel,
  });

  /// The allocation being topped up, or null when the task has none yet (the
  /// top-up then DEFINES it — see [previewTaskTopUp]).
  final double? allocated;

  /// The currency the preview renders in (the task's project currency).
  final String currency;

  /// Called with the new allocation once the amount validates. Never called
  /// with a rejected value — nothing is written optimistically.
  final ValueChanged<double> onCommit;

  final VoidCallback onCancel;

  @override
  State<TaskTopUpEditor> createState() => _TaskTopUpEditorState();
}

class _TaskTopUpEditorState extends State<TaskTopUpEditor> {
  final TextEditingController _amountController = TextEditingController();

  /// The last REJECTION, shown under the field. Deliberately only set on a
  /// submit attempt and cleared on the next keystroke: validating live would
  /// flash "must be more than 0" at someone who has typed the minus sign of
  /// nothing in particular. The PREVIEW below the field is the live feedback.
  String? _error;

  @override
  void dispose() {
    _amountController.dispose();
    super.dispose();
  }

  void _submit() {
    final preview = previewTaskTopUp(_amountController.text, widget.allocated);
    final total = preview.total;
    if (total == null) {
      setState(() => _error = preview.error);
      return;
    }
    widget.onCommit(total);
  }

  @override
  Widget build(BuildContext context) {
    final preview = previewTaskTopUp(_amountController.text, widget.allocated);
    final label = taskTopUpPreviewLabel(
      widget.allocated,
      preview.total,
      widget.currency,
    );
    return LzCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        mainAxisSize: MainAxisSize.min,
        children: [
          LzTextField(
            controller: _amountController,
            fieldKey: kTaskTopUpFieldKey,
            label: 'Top up by',
            hint: '0.00',
            prefixIcon: Icons.savings_outlined,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            textInputAction: TextInputAction.done,
            autofocus: true,
            errorText: _error,
            // Recompute the preview on every keystroke, and drop a stale
            // rejection the moment the user starts fixing it.
            onChanged: (_) => setState(() => _error = null),
            onSubmitted: (_) => _submit(),
          ),
          const SizedBox(height: AppSpacing.sm),
          Text(
            label,
            key: kTaskTopUpPreviewKey,
            style: AppText.caption.copyWith(
              color: preview.total == null
                  ? AppColors.textMuted
                  : AppColors.accent,
              fontWeight: preview.total == null
                  ? FontWeight.w500
                  : FontWeight.w700,
            ),
          ),
          const SizedBox(height: AppSpacing.md),
          Row(
            children: [
              Expanded(
                child: LzButton.primary(
                  key: kTaskTopUpSubmitKey,
                  label: 'Top up',
                  icon: Icons.add_rounded,
                  expand: true,
                  onPressed: _submit,
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: LzButton.ghost(
                  key: kTaskTopUpCancelKey,
                  label: 'Cancel',
                  onPressed: widget.onCancel,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
