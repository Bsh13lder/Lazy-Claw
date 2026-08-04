/// The expense detail sheet's three linked dropdowns: PROJECT → TASK →
/// SUB-TASK.
///
/// Extracted out of `expense_detail_sheet.dart` verbatim — rules, guards and
/// comments unchanged. They were ~275 lines of one repeated widget shape in a
/// file that had to make room for auto-save, and they are genuinely separable:
/// each is a pure render of a selection plus a stale-id guard, with every
/// decision about what may be SUBMITTED still living on the sheet.
///
/// The `onStaleSelection` callbacks are the load-bearing part. A picker that
/// silently falls back to "nothing selected" while the owning state still
/// holds the old id is how a Save resubmits an id the UI stopped showing —
/// which the server 400s, dropping the whole patch (amount edit included).
library;

import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../models/project.dart';
import '../../models/subtask.dart';
import '../../models/task.dart';

/// A token-styled dropdown project picker — visually identical to the add-expense
/// sheet's picker so the two surfaces feel like one family.
class ExpenseProjectPicker extends StatelessWidget {
  const ExpenseProjectPicker({
    super.key,
    required this.projects,
    required this.selectedId,
    required this.onChanged,
    this.onStaleSelection,
  });

  final List<Project> projects;
  final String? selectedId;
  final ValueChanged<String?> onChanged;

  /// Invoked (once, post-frame) when [selectedId] is non-null but isn't
  /// among [projects] — i.e. the guard below is about to render "no project
  /// selected" even though the caller still thinks one is selected. Lets the
  /// owning state reset its own field to match what's actually on screen, so
  /// a Save action can never resubmit an id the UI stopped displaying.
  final VoidCallback? onStaleSelection;

  @override
  Widget build(BuildContext context) {
    // Guard against a stale selectedId that isn't in the list (e.g. its project
    // was deleted) so the DropdownButton's assert doesn't fire.
    final hasSelected = projects.any((p) => p.id == selectedId);
    if (selectedId != null && !hasSelected) {
      WidgetsBinding.instance
          .addPostFrameCallback((_) => onStaleSelection?.call());
    }

    if (projects.isEmpty) {
      return Container(
        padding: const EdgeInsets.all(AppSpacing.md),
        decoration: BoxDecoration(
          color: AppColors.bgSurfaceElevated,
          borderRadius: AppRadii.rMd,
          border: Border.all(color: AppColors.borderDefault),
        ),
        child: Text(
          'No projects',
          style: AppText.body.copyWith(color: AppColors.textMuted),
        ),
      );
    }

    final value = hasSelected ? selectedId : null;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Project',
          style: AppText.label.copyWith(color: AppColors.textSecondary),
        ),
        const SizedBox(height: AppSpacing.sm),
        Container(
          decoration: BoxDecoration(
            color: AppColors.bgSurfaceElevated,
            borderRadius: AppRadii.rMd,
            border: Border.all(color: AppColors.borderDefault),
          ),
          child: DropdownButtonHideUnderline(
            child: DropdownButton<String>(
              key: const Key('expense-detail-project'),
              value: value,
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
      ],
    );
  }
}

/// A token-styled dropdown task picker — links this expense to one of the
/// tasks in its (already-selected) project. "(no task)" is always the first
/// option and represents an explicit clear, not "leave unchanged" (the sheet
/// always saves with `taskIdSet: true`). Visually mirrors [ExpenseProjectPicker] so
/// the two feel like one family.
class ExpenseTaskPicker extends StatelessWidget {
  const ExpenseTaskPicker({
    super.key,
    required this.tasks,
    required this.selectedId,
    required this.onChanged,
    this.onStaleSelection,
  });

  final List<Task> tasks;
  final String? selectedId;
  final ValueChanged<String?> onChanged;

  /// Invoked (once, post-frame) when [selectedId] is non-null but isn't
  /// among [tasks] — e.g. the linked task was deleted, or its project
  /// changed. NOT triggered merely because the linked task is done: the
  /// caller (`_tasksForCurrentProject`) always keeps the current [selectedId]
  /// in [tasks] even once it's excluded from fresh picks, so completing a
  /// task can never silently clear an expense's existing link to it. Mirrors
  /// [ExpenseProjectPicker.onStaleSelection]: lets the owning state reset its own
  /// field so render and submitted value can never diverge.
  final VoidCallback? onStaleSelection;

  @override
  Widget build(BuildContext context) {
    // Guard against a stale selectedId that isn't among the current options
    // (e.g. its task was deleted, or the project changed since) so the
    // DropdownButton's assert doesn't fire — mirrors ExpenseProjectPicker's guard.
    final hasSelected = tasks.any((t) => t.id == selectedId);
    if (selectedId != null && !hasSelected) {
      WidgetsBinding.instance
          .addPostFrameCallback((_) => onStaleSelection?.call());
    }
    final value = hasSelected ? selectedId : null;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Task (optional)',
          style: AppText.label.copyWith(color: AppColors.textSecondary),
        ),
        const SizedBox(height: AppSpacing.sm),
        Container(
          decoration: BoxDecoration(
            color: AppColors.bgSurfaceElevated,
            borderRadius: AppRadii.rMd,
            border: Border.all(color: AppColors.borderDefault),
          ),
          child: DropdownButtonHideUnderline(
            child: DropdownButton<String>(
              key: const Key('expense-detail-task'),
              value: value,
              isExpanded: true,
              padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
              dropdownColor: AppColors.bgSurfaceElevated,
              style: AppText.body,
              icon: const Icon(
                Icons.keyboard_arrow_down_rounded,
                color: AppColors.textMuted,
              ),
              items: [
                DropdownMenuItem<String>(
                  value: null,
                  child: Text(
                    '(no task)',
                    style: AppText.body.copyWith(color: AppColors.textMuted),
                  ),
                ),
                for (final t in tasks)
                  DropdownMenuItem<String>(
                    value: t.id,
                    child: Text(
                      t.title,
                      style: AppText.body,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
              ],
              onChanged: onChanged,
            ),
          ),
        ),
      ],
    );
  }
}

/// A token-styled dropdown sub-task picker — links this expense to one
/// checklist item of the (already-selected) task. "No subtask" is always the
/// first option and represents an explicit clear, not "leave unchanged" (the
/// sheet always saves with `subtaskIdSet: true`, mirroring [ExpenseTaskPicker]'s
/// own `taskIdSet: true`). Disabled (greyed hint, no items, `onChanged: null`)
/// until a task is selected — a sub-task can't exist without one. Visually
/// mirrors [ExpenseTaskPicker] so all three pickers feel like one family.
class ExpenseSubtaskPicker extends StatelessWidget {
  const ExpenseSubtaskPicker({
    super.key,
    required this.subtasks,
    required this.selectedId,
    required this.enabled,
    required this.onChanged,
  });

  final List<Subtask> subtasks;
  final String? selectedId;
  final bool enabled;
  final ValueChanged<String?> onChanged;

  @override
  Widget build(BuildContext context) {
    // Guard against a stale selectedId that isn't among the current options
    // (e.g. its sub-task was deleted, or the task changed since) so the
    // DropdownButton's assert doesn't fire — mirrors ExpenseTaskPicker's guard.
    final hasSelected = enabled && subtasks.any((s) => s.id == selectedId);
    final value = hasSelected ? selectedId : null;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Subtask (optional)',
          style: AppText.label.copyWith(color: AppColors.textSecondary),
        ),
        const SizedBox(height: AppSpacing.sm),
        Container(
          decoration: BoxDecoration(
            color: AppColors.bgSurfaceElevated,
            borderRadius: AppRadii.rMd,
            border: Border.all(color: AppColors.borderDefault),
          ),
          child: DropdownButtonHideUnderline(
            child: DropdownButton<String>(
              key: const Key('expense-detail-subtask'),
              value: value,
              isExpanded: true,
              padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
              dropdownColor: AppColors.bgSurfaceElevated,
              style: AppText.body,
              icon: const Icon(
                Icons.keyboard_arrow_down_rounded,
                color: AppColors.textMuted,
              ),
              hint: Text(
                enabled ? 'Select subtask' : 'Select a task first',
                style: AppText.body.copyWith(color: AppColors.textMuted),
              ),
              items: !enabled
                  ? const []
                  : [
                      DropdownMenuItem<String>(
                        value: null,
                        child: Text(
                          'No subtask',
                          style: AppText.body.copyWith(
                            color: AppColors.textMuted,
                          ),
                        ),
                      ),
                      for (final s in subtasks)
                        DropdownMenuItem<String>(
                          value: s.id,
                          child: Text(
                            s.title,
                            style: AppText.body,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                    ],
              onChanged: enabled ? onChanged : null,
            ),
          ),
        ),
      ],
    );
  }
}

/// A small uppercase section label matching the task detail sheet's headers.
class ExpenseSectionLabel extends StatelessWidget {
  const ExpenseSectionLabel(this.text, {super.key});
  final String text;

  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      style: AppText.caption.copyWith(
        color: AppColors.textMuted,
        letterSpacing: 0.8,
        fontWeight: FontWeight.w700,
      ),
    );
  }
}

