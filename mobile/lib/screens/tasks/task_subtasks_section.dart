/// The task detail sheet's SUBTASKS block: the header + progress label and
/// the [SubtaskEditor] itself.
///
/// Extracted out of `task_detail_sheet.dart` alongside [TaskDueSection] and
/// [TaskRepeatSection], for the same reason (that file was past the 800-line
/// ceiling). Owns no state — the parent still holds the working checklist and
/// receives a fresh immutable list on every change.
library;

import 'package:flutter/material.dart';

import '../../models/subtask.dart';
import '../../ui/ui.dart';
import 'subtask_editor.dart';
import 'task_section_label.dart';

class TaskSubtasksSection extends StatelessWidget {
  const TaskSubtasksSection({
    super.key,
    required this.subtasks,
    required this.onChanged,
    required this.commentCounts,
    required this.expenseTotals,
    required this.expenseCurrency,
    required this.savedSubtaskIds,
    required this.onAddExpense,
    required this.onOpenComments,
  });

  /// The parent's WORKING list (may contain not-yet-saved rows).
  final List<Subtask> subtasks;
  final ValueChanged<List<Subtask>> onChanged;

  /// See [SubtaskEditor] for what each of these gates — in particular why
  /// [commentCounts]'s key set and [savedSubtaskIds] are keyed off the SAVED
  /// task rather than off [subtasks].
  final Map<String, int> commentCounts;
  final Map<String, double> expenseTotals;
  final String expenseCurrency;
  final Set<String> savedSubtaskIds;
  final ValueChanged<String> onAddExpense;
  final ValueChanged<String> onOpenComments;

  @override
  Widget build(BuildContext context) {
    final progress = subtaskProgressLabel(subtasks);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        Row(
          children: [
            TaskSectionLabel('SUBTASKS'),
            const Spacer(),
            if (progress != null)
              Text(
                progress,
                key: const Key('task-detail-subtask-progress'),
                style: AppText.caption.copyWith(color: AppColors.accent),
              ),
          ],
        ),
        const SizedBox(height: AppSpacing.sm),
        SubtaskEditor(
          subtasks: subtasks,
          onChanged: onChanged,
          commentCounts: commentCounts,
          expenseTotals: expenseTotals,
          expenseCurrency: expenseCurrency,
          savedSubtaskIds: savedSubtaskIds,
          onAddExpense: onAddExpense,
          onOpenComments: onOpenComments,
        ),
      ],
    );
  }
}
