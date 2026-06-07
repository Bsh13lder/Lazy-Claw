import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/models/expense.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import 'budget_math.dart';
import 'expense_row.dart';
import 'money_helpers.dart';
import 'project_color_picker.dart';

/// Expandable project card with per-project traffic-light budget bar and
/// an inline expense list when expanded.
class ProjectCard extends StatefulWidget {
  const ProjectCard({
    super.key,
    required this.project,
    required this.expenses,
    required this.pendingSync,
    required this.onDelete,
    this.onEdit,
    this.onToggleFavorite,
  });

  final Project project;

  /// Expenses belonging to this project (pre-filtered by caller).
  final List<Expense> expenses;
  final bool pendingSync;
  final VoidCallback onDelete;

  /// Opens the manage-project sheet (rename / budget / color / delete). When
  /// null the edit affordance is hidden.
  final VoidCallback? onEdit;

  /// Flips the project's favorite flag. When null the star affordance is hidden.
  final VoidCallback? onToggleFavorite;

  @override
  State<ProjectCard> createState() => _ProjectCardState();
}

class _ProjectCardState extends State<ProjectCard> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final project = widget.project;
    final budget = project.budget;
    // Derive spend from the very expenses this card renders, so the bar can
    // never disagree with the inline ledger (and is correct offline regardless
    // of whether the project row carries a server rollup).
    final spent = spentForProject(project.id, widget.expenses);
    final fraction = budget > 0 ? (spent / budget).clamp(0.0, 1.0) : 0.0;
    final overBudget = budget > 0 && spent > budget;

    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.xs,
      ),
      child: Dismissible(
        key: ValueKey('project-${project.id}'),
        direction: DismissDirection.endToStart,
        background: Container(
          alignment: Alignment.centerRight,
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
          decoration: BoxDecoration(
            color: AppColors.error.withValues(alpha: 0.15),
            borderRadius: AppRadii.rLg,
            border: Border.all(color: AppColors.error.withValues(alpha: 0.3)),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.delete_outline,
                  color: AppColors.error, size: 20),
              const SizedBox(width: AppSpacing.xs),
              Text(
                'Delete',
                style:
                    AppText.caption.copyWith(color: AppColors.error),
              ),
            ],
          ),
        ),
        confirmDismiss: (_) => _confirmDelete(context, project.name),
        onDismissed: (_) => widget.onDelete(),
        child: LzCard(
          padding: EdgeInsets.zero,
          onTap: widget.expenses.isNotEmpty
              ? () => setState(() => _expanded = !_expanded)
              : null,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Padding(
                padding: const EdgeInsets.all(AppSpacing.lg),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    // Header row: color dot + name + badges + edit.
                    Row(
                      children: [
                        ProjectColorDot(hex: project.color),
                        const SizedBox(width: AppSpacing.sm),
                        Expanded(
                          child: Text(
                            project.name,
                            style: AppText.title,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                        if (widget.pendingSync) ...[
                          LzSyncBadge(
                            state: LzSyncState.offline,
                            compact: true,
                          ),
                          const SizedBox(width: AppSpacing.sm),
                        ],
                        if (project.isArchived)
                          LzChip(
                            label: 'Archived',
                            dense: true,
                            color: AppColors.textMuted,
                          )
                        else if (widget.expenses.isNotEmpty) ...[
                          Icon(
                            _expanded
                                ? Icons.keyboard_arrow_up_rounded
                                : Icons.keyboard_arrow_down_rounded,
                            color: AppColors.textMuted,
                            size: 20,
                          ),
                        ],
                        if (widget.onToggleFavorite != null) ...[
                          const SizedBox(width: AppSpacing.xs),
                          GestureDetector(
                            onTap: widget.onToggleFavorite,
                            behavior: HitTestBehavior.opaque,
                            child: Padding(
                              padding: const EdgeInsets.all(AppSpacing.xs),
                              child: Icon(
                                project.isFavorite
                                    ? Icons.star_rounded
                                    : Icons.star_outline_rounded,
                                color: project.isFavorite
                                    ? AppColors.warn
                                    : AppColors.textMuted,
                                size: 20,
                                semanticLabel: project.isFavorite
                                    ? 'Unfavorite project'
                                    : 'Favorite project',
                              ),
                            ),
                          ),
                        ],
                        if (widget.onEdit != null) ...[
                          const SizedBox(width: AppSpacing.xs),
                          GestureDetector(
                            onTap: widget.onEdit,
                            behavior: HitTestBehavior.opaque,
                            child: Padding(
                              padding: const EdgeInsets.all(AppSpacing.xs),
                              child: Icon(
                                Icons.more_horiz_rounded,
                                color: AppColors.textMuted,
                                size: 20,
                              ),
                            ),
                          ),
                        ],
                      ],
                    ),
                    const SizedBox(height: AppSpacing.md),
                    // Budget bar.
                    if (budget > 0) ...[
                      LzProgressBar(
                        value: fraction,
                        height: 8,
                        trafficLight: true,
                      ),
                      const SizedBox(height: AppSpacing.sm),
                      Row(
                        children: [
                          Text(
                            fmtMoney(project.currency, spent),
                            style: AppText.label.copyWith(
                              color: AppColors.trafficLight(fraction),
                            ),
                          ),
                          const SizedBox(width: AppSpacing.xs),
                          Text(
                            'spent',
                            style: AppText.caption,
                          ),
                          const Spacer(),
                          Text(
                            overBudget
                                ? '${fmtMoney(project.currency, spent - budget)} over'
                                : '${fmtMoney(project.currency, budget - spent)} left',
                            style: AppText.caption.copyWith(
                              color: overBudget
                                  ? AppColors.error
                                  : AppColors.textMuted,
                            ),
                          ),
                          const SizedBox(width: AppSpacing.xs),
                          Text(
                            '/ ${fmtMoney(project.currency, budget)}',
                            style: AppText.caption.copyWith(
                              color: AppColors.textMuted,
                            ),
                          ),
                        ],
                      ),
                    ] else ...[
                      Row(
                        children: [
                          Text(
                            fmtMoney(project.currency, spent),
                            style: AppText.label
                                .copyWith(color: AppColors.accent),
                          ),
                          const SizedBox(width: AppSpacing.xs),
                          Text(
                            'total spend',
                            style: AppText.caption,
                          ),
                          const Spacer(),
                          Text(
                            'No budget',
                            style: AppText.caption
                                .copyWith(color: AppColors.textMuted),
                          ),
                        ],
                      ),
                    ],
                  ],
                ),
              ),
              // Inline expense list (expanded state).
              if (_expanded && widget.expenses.isNotEmpty) ...[
                Container(
                  height: 0.5,
                  color: AppColors.borderSubtle,
                ),
                ...widget.expenses
                    .where((e) => !e.isVoid)
                    .map(
                      (e) => ExpenseRow(
                        expense: e,
                        projects: const [],
                        showProject: false,
                        pendingSync: false,
                        onDelete: null,
                      ),
                    ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Future<bool> _confirmDelete(BuildContext context, String name) async {
    return await showDialog<bool>(
          context: context,
          builder: (ctx) => AlertDialog(
            backgroundColor: AppColors.bgSurfaceElevated,
            title: Text(
              'Delete project?',
              style: AppText.title,
            ),
            content: Text(
              '"$name" and all its expenses will be removed.',
              style: AppText.body,
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(ctx, false),
                child: Text(
                  'Cancel',
                  style: AppText.label
                      .copyWith(color: AppColors.textSecondary),
                ),
              ),
              TextButton(
                onPressed: () => Navigator.pop(ctx, true),
                child: Text(
                  'Delete',
                  style: AppText.label.copyWith(color: AppColors.error),
                ),
              ),
            ],
          ),
        ) ??
        false;
  }
}
