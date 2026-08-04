/// The task detail sheet's HEADER block: save state, the destructive action,
/// the title field, and the record's own timestamps.
///
/// Extracted out of `task_detail_sheet.dart` — verbatim rules and comments —
/// for the reason every other block in this folder was ([TaskDueSection],
/// [TaskRepeatSection], `task_detail_live_data.dart`): that file sits on this
/// project's 800-line ceiling, and surfacing the created/completed times had
/// to be paid for by moving something out rather than by growing past it.
///
/// Owns no state: the sheet still holds the controller, the validation error
/// and the autosave status, and hands them down.
library;

import 'package:flutter/material.dart';

import '../../core/autosave.dart';
import '../../ui/ui.dart';
import '../../widgets/autosave_indicator.dart';
import 'task_attribute_chips.dart';
import 'task_timestamps.dart';

class TaskDetailHeader extends StatelessWidget {
  const TaskDetailHeader({
    super.key,
    required this.titleController,
    required this.titleError,
    required this.status,
    required this.deleting,
    required this.onDelete,
    required this.createdAt,
    required this.completedAt,
    required this.isDone,
    this.now,
  });

  final TextEditingController titleController;

  /// The inline rejection under the TITLE (null = valid).
  final String? titleError;

  final AutosaveStatus status;
  final bool deleting;
  final VoidCallback onDelete;

  /// The task's own `created_at` — always present on a real row, so the
  /// created line is effectively unconditional here.
  final String createdAt;

  /// The task's `completed_at`, which OUTLIVES a re-open: the column keeps the
  /// old value while `status` goes back to `todo`. [isDone] is what decides
  /// whether it may be shown, and that decision lives here rather than at the
  /// call site so it can't be forgotten by the next caller.
  final String? completedAt;
  final bool isDone;

  /// Injectable clock for tests — see [TaskTimestampsLine.now].
  final DateTime? now;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // ── Save state, then the destructive action ────────────────────
        //
        // The indicator sits at the TOP because that is where the eye
        // starts, and it is the only remaining evidence that typing here
        // does anything. Delete keeps the far corner from the submit: this
        // sheet is thumb-driven and "one mis-tap deletes the task" is not
        // an acceptable failure mode. It is still behind a confirm.
        Row(
          children: [
            AutosaveIndicator(status: status),
            const Spacer(),
            TaskDeleteAction(
              deleting: deleting,
              onPressed: status == AutosaveStatus.saving ? null : onDelete,
            ),
          ],
        ),

        // ── Title ──────────────────────────────────────────────────────
        LzTextField(
          controller: titleController,
          fieldKey: const Key('task-detail-title'),
          hint: 'What needs to be done?',
          prefixIcon: Icons.task_alt_outlined,
          textInputAction: TextInputAction.next,
          errorText: titleError,
        ),

        // ── When it was made, and when it was finished ─────────────────
        //
        // Directly under the title, muted: it is the record's provenance,
        // so it belongs to the title rather than to any of the editable
        // controls below — and it is readable the moment the sheet opens
        // instead of after scrolling the app's longest form.
        TaskTimestampsLine(
          keyPrefix: 'task-detail',
          createdAt: createdAt,
          completedAt: isDone ? completedAt : null,
          now: now,
          padding: const EdgeInsets.only(
            top: AppSpacing.sm,
            left: AppSpacing.xs,
          ),
        ),
      ],
    );
  }
}
