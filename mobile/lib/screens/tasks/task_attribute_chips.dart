/// The task detail sheet's PRIORITY and PROJECT & TAGS chip rows, plus the
/// de-emphasized Delete affordance that heads the sheet.
///
/// All three are small, self-contained pieces of the sheet's chrome that were
/// inflating one already-oversized `build()`. None owns state.
library;

import 'package:flutter/material.dart';

import '../../models/project.dart';
import '../../ui/ui.dart';
import 'chip_edit.dart';
import 'task_section_label.dart';
import 'task_tags_field.dart';

/// The four priority chips.
///
/// Wrap, not Row: four chips overflow a ~360pt phone by a few pixels (a real
/// RenderFlex overflow, caught by the short-viewport test in
/// `task_detail_save_test.dart`). Reflowing beats clipping.
class TaskPriorityChips extends StatelessWidget {
  const TaskPriorityChips({
    super.key,
    required this.priority,
    required this.onChanged,
  });

  final String priority;
  final ValueChanged<String> onChanged;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        TaskSectionLabel('PRIORITY'),
        const SizedBox(height: AppSpacing.sm),
        Wrap(
          spacing: AppSpacing.sm,
          runSpacing: AppSpacing.sm,
          children: [
            for (final p in kTaskPriorities)
              LzChip(
                label: p,
                selected: p == priority,
                color: priorityColor(p),
                onTap: () => onChanged(p),
              ),
          ],
        ),
      ],
    );
  }
}

/// PROJECT and TAGS side by side, in one matching chip style.
///
/// TAGS used to be its own three-block section (label + chip Wrap + an
/// always-visible text field) and dominated the sheet for a field most edits
/// never touch. It is now a summary chip beside PROJECT; the editing surface
/// lives in [showTaskTagsSheet]. A Wrap (not a Row) so a long project name
/// plus a long tag summary reflow instead of overflowing on a narrow phone.
class TaskProjectTagsRow extends StatelessWidget {
  const TaskProjectTagsRow({
    super.key,
    required this.projects,
    required this.category,
    required this.tags,
    required this.onPickProject,
    required this.onOpenTags,
  });

  final List<Project> projects;
  final String? category;
  final List<String> tags;
  final VoidCallback onPickProject;
  final VoidCallback onOpenTags;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        TaskSectionLabel('PROJECT & TAGS'),
        const SizedBox(height: AppSpacing.sm),
        Wrap(
          spacing: AppSpacing.sm,
          runSpacing: AppSpacing.sm,
          children: [
            ProjectChip(
              fieldKey: const Key('task-detail-project'),
              projects: projects,
              category: category,
              onTap: onPickProject,
            ),
            TaskTagsChip(tags: tags, onTap: onOpenTags),
          ],
        ),
      ],
    );
  }
}

/// The muted, icon-only Delete affordance.
///
/// WHY it looks like this: with Save moved to a bottom-right floating target
/// the detail sheet is thumb-driven, and the old footer put `Delete Task`
/// shoulder-to-shoulder with it. "One mis-tap destroys the task" is not an
/// acceptable failure mode, so this is rendered small, muted and icon-only at
/// the sheet's TOP-RIGHT — the furthest corner from the submit — and still
/// sits behind a confirm dialog.
///
/// [deleting] swaps in a spinner, which is also what makes a second tap
/// impossible mid-delete.
class TaskDeleteAction extends StatelessWidget {
  const TaskDeleteAction({
    super.key,
    required this.deleting,
    required this.onPressed,
  });

  final bool deleting;

  /// Null disables the button (e.g. while a Save is in flight).
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    if (deleting) {
      return const Padding(
        padding: EdgeInsets.all(AppSpacing.sm),
        child: SizedBox(
          width: 18,
          height: 18,
          child: CircularProgressIndicator(
            strokeWidth: 2,
            color: AppColors.textMuted,
          ),
        ),
      );
    }
    return LzIconButton(
      key: const Key('task-detail-delete'),
      icon: Icons.delete_outline,
      tooltip: 'Delete task',
      size: 18,
      color: AppColors.textMuted,
      onPressed: onPressed,
    );
  }
}
