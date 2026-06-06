import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/models/expense.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import 'money_helpers.dart';

/// A single expense ledger row.
///
/// Supports optional swipe-to-delete (pass [onDelete]) and an optional
/// project tag chip (pass [showProject] = true with a non-empty [projects]).
///
/// Tapping the row body fires [onTap] — the Money screen wires this to open the
/// expense detail/edit sheet. Null leaves the row non-tappable. The swipe-to-
/// delete gesture is unchanged.
class ExpenseRow extends StatelessWidget {
  const ExpenseRow({
    super.key,
    required this.expense,
    required this.projects,
    required this.pendingSync,
    required this.onDelete,
    this.showProject = true,
    this.onTap,
  });

  final Expense expense;
  final List<Project> projects;
  final bool pendingSync;
  final VoidCallback? onDelete;
  final bool showProject;

  /// Opens the expense detail/edit sheet. Null leaves the row non-tappable.
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final projectName = _resolveProjectName();
    final desc = expense.displayDescription;
    final vendor = expense.vendor ?? '';

    final inner = _buildContent(context, desc, vendor, projectName);
    final content = onTap == null
        ? inner
        : InkWell(onTap: onTap, child: inner);

    if (onDelete == null) {
      return content;
    }

    return Dismissible(
      key: ValueKey('expense-${expense.id}'),
      direction: DismissDirection.endToStart,
      background: Container(
        alignment: Alignment.centerRight,
        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
        color: AppColors.error.withValues(alpha: 0.12),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.delete_outline, color: AppColors.error, size: 18),
            const SizedBox(width: AppSpacing.xs),
            Text(
              'Delete',
              style: AppText.caption.copyWith(color: AppColors.error),
            ),
          ],
        ),
      ),
      confirmDismiss: (_) => _confirmDelete(context),
      onDismissed: (_) => onDelete!(),
      child: content,
    );
  }

  Widget _buildContent(
    BuildContext context,
    String desc,
    String vendor,
    String? projectName,
  ) {
    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.sm,
      ),
      child: Row(
        children: [
          // Leading receipt icon container.
          Container(
            width: 40,
            height: 40,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: AppColors.bgSurfaceElevated,
              borderRadius: AppRadii.rMd,
              border: Border.all(color: AppColors.borderSubtle),
            ),
            child: const Icon(
              Icons.receipt_outlined,
              size: 18,
              color: AppColors.textMuted,
            ),
          ),
          const SizedBox(width: AppSpacing.md),
          // Description + project tag.
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  desc.isEmpty ? '(no description)' : desc,
                  style: AppText.body,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                if (vendor.isNotEmpty && vendor != desc) ...[
                  const SizedBox(height: AppSpacing.xs),
                  Text(
                    vendor,
                    style: AppText.caption,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
                if (showProject && projectName != null && projectName.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: AppSpacing.xs),
                    child: LzChip(
                      label: projectName,
                      dense: true,
                      color: AppColors.accent,
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          // Amount + sync badge.
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                fmtMoney(expense.currency, expense.amount),
                style: AppText.label.copyWith(color: AppColors.accent),
              ),
              if (pendingSync) ...[
                const SizedBox(height: AppSpacing.xs),
                LzSyncBadge(state: LzSyncState.offline, compact: true),
              ],
            ],
          ),
        ],
      ),
    );
  }

  String? _resolveProjectName() {
    if (!showProject) return null;
    if (expense.projectName?.isNotEmpty == true) return expense.projectName;
    return projects
        .where((p) => p.id == expense.projectId)
        .map((p) => p.name)
        .firstOrNull;
  }

  Future<bool> _confirmDelete(BuildContext context) async {
    final desc = expense.displayDescription;
    return await showDialog<bool>(
          context: context,
          builder: (ctx) => AlertDialog(
            backgroundColor: AppColors.bgSurfaceElevated,
            title: Text('Delete expense?', style: AppText.title),
            content: Text(
              desc.isEmpty ? 'This expense will be removed.' : '"$desc"',
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
