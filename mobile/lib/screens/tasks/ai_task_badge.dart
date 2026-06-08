import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import '../../models/task.dart';
import 'task_owner_filter.dart';

/// A tiny "AI" pill that marks an agent-created task so it's identifiable even
/// in the combined "All" view. Tonal info-colored, kit-only (no hard-coded
/// colors / sizes / text styles).
///
/// Rendered as a left-aligned strip ABOVE the task card by [AgentTaskBadged]
/// rather than inside [TaskRow] (which this feature must not modify), so the
/// row widget stays untouched while AI tasks still read distinctly.
class AiTaskBadge extends StatelessWidget {
  const AiTaskBadge({super.key});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.sm,
        vertical: 2,
      ),
      decoration: BoxDecoration(
        color: AppColors.info.withValues(alpha: 0.16),
        borderRadius: AppRadii.rPill,
        border: Border.all(color: AppColors.info.withValues(alpha: 0.5)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.auto_awesome, size: 11, color: AppColors.info),
          const SizedBox(width: AppSpacing.xs),
          Text(
            'AI',
            style: AppText.caption.copyWith(
              color: AppColors.info,
              fontWeight: FontWeight.w700,
              height: 1.0,
            ),
          ),
        ],
      ),
    );
  }
}

/// Wraps [child] (a task row) and, when [task] was AI-created, prepends a small
/// [AiTaskBadge] strip so agent tasks are identifiable in any list. Non-agent
/// tasks render [child] verbatim (zero layout cost).
class AgentTaskBadged extends StatelessWidget {
  const AgentTaskBadged({super.key, required this.task, required this.child});

  final Task task;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    if (!isAgentTask(task)) return child;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Padding(
          padding: EdgeInsets.only(left: AppSpacing.xs, bottom: AppSpacing.xs),
          child: AiTaskBadge(),
        ),
        child,
      ],
    );
  }
}
