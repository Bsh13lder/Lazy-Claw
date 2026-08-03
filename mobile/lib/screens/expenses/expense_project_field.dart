import 'package:flutter/material.dart';

import '../../models/project.dart';
import '../../ui/ui.dart';

/// The Add Expense sheet's project control, in its two modes.
///
/// Extracted from `add_expense_sheet.dart` so that file stays focused on the
/// form's behavior (smart parsing, submit) instead of growing a second screen's
/// worth of layout.

/// Editable project selector — the default (unscoped) Add Expense flow, where
/// the user decides which project the expense lands in.
class ExpenseProjectPicker extends StatelessWidget {
  const ExpenseProjectPicker({
    super.key,
    required this.projects,
    required this.selectedId,
    required this.onChanged,
  });

  final List<Project> projects;
  final String? selectedId;
  final ValueChanged<String?> onChanged;

  @override
  Widget build(BuildContext context) {
    if (projects.isEmpty) {
      return Container(
        padding: const EdgeInsets.all(AppSpacing.md),
        decoration: BoxDecoration(
          color: AppColors.bgSurfaceElevated,
          borderRadius: AppRadii.rMd,
          border: Border.all(color: AppColors.borderDefault),
        ),
        child: Text(
          'No projects — create one first',
          style: AppText.body.copyWith(color: AppColors.textMuted),
        ),
      );
    }

    return _LabeledField(
      child: Container(
        decoration: BoxDecoration(
          color: AppColors.bgSurfaceElevated,
          borderRadius: AppRadii.rMd,
          border: Border.all(color: AppColors.borderDefault),
        ),
        child: DropdownButtonHideUnderline(
          child: DropdownButton<String>(
            value: selectedId,
            isExpanded: true,
            padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
            dropdownColor: AppColors.bgSurfaceElevated,
            style: AppText.body,
            icon: const Icon(
              Icons.keyboard_arrow_down_rounded,
              color: AppColors.textMuted,
            ),
            hint: Text(
              'Select project',
              style: AppText.body.copyWith(color: AppColors.textMuted),
            ),
            items: projects
                .map(
                  (p) => DropdownMenuItem<String>(
                    value: p.id,
                    child: Text(p.name, style: AppText.body),
                  ),
                )
                .toList(),
            onChanged: onChanged,
          ),
        ),
      ),
    );
  }
}

/// Read-only project readout for TASK-SCOPED mode.
///
/// When the expense is being filed against a task (or sub-task), the project
/// is DERIVED from that task — it is not the user's to choose here. Offering
/// an editable picker would let someone file the expense under a different
/// project than the task's own, which silently corrupts both the project
/// budget rollup and the per-task / per-sub-task expense totals (they are
/// summed from different columns and would stop agreeing). So the value is
/// shown, locked, with a lock affordance explaining why it can't be touched.
class ExpenseProjectLockedField extends StatelessWidget {
  const ExpenseProjectLockedField({
    super.key,
    required this.projectName,
  });

  /// Resolved display name. Falls back to a neutral label when the project
  /// isn't in the locally cached list yet (fresh install / mid-sync) — never
  /// render a raw id at the user.
  final String? projectName;

  @override
  Widget build(BuildContext context) {
    return _LabeledField(
      child: Container(
        key: const Key('expense-project-locked'),
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.md,
          vertical: AppSpacing.md,
        ),
        decoration: BoxDecoration(
          color: AppColors.bgSurfaceElevated,
          borderRadius: AppRadii.rMd,
          border: Border.all(color: AppColors.borderSubtle),
        ),
        child: Row(
          children: [
            const Icon(
              Icons.lock_outline_rounded,
              size: 16,
              color: AppColors.textMuted,
            ),
            const SizedBox(width: AppSpacing.sm),
            Expanded(
              child: Text(
                projectName ?? "The task's project",
                style: AppText.body.copyWith(color: AppColors.textSecondary),
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Shared "Project" caption + gap so both modes line up pixel-for-pixel.
class _LabeledField extends StatelessWidget {
  const _LabeledField({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Project',
          style: AppText.label.copyWith(color: AppColors.textSecondary),
        ),
        const SizedBox(height: AppSpacing.sm),
        child,
      ],
    );
  }
}
