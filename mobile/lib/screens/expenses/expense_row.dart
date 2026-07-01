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
    this.onToggleFavorite,
  });

  final Expense expense;
  final List<Project> projects;
  final bool pendingSync;
  final VoidCallback? onDelete;
  final bool showProject;

  /// Opens the expense detail/edit sheet. Null leaves the row non-tappable.
  final VoidCallback? onTap;

  /// Flips the expense's favorite (star) flag. When null the star affordance is
  /// hidden — e.g. the read-only rows nested inside a project card.
  final VoidCallback? onToggleFavorite;

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
    // When the row was recorded. Prefer the server `created_at`; fall back to
    // `spent_at` (a fresh local row stamps created_at, so this only matters for
    // older/partial server payloads). Null → render nothing.
    final savedLabel =
        formatSavedAt(expense.createdAt) ?? formatSavedAt(expense.spentAt);
    final metaText = _metaText(vendor, desc, savedLabel);

    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.md,
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
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
              Icons.receipt_long_outlined,
              size: 18,
              color: AppColors.textMuted,
            ),
          ),
          const SizedBox(width: AppSpacing.md),
          // Description + muted metadata line (vendor · saved time).
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        desc.isEmpty ? '(no description)' : desc,
                        style: AppText.body.copyWith(
                          fontWeight: FontWeight.w600,
                        ),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    if (showProject &&
                        projectName != null &&
                        projectName.isNotEmpty) ...[
                      const SizedBox(width: AppSpacing.sm),
                      LzChip(
                        label: projectName,
                        dense: true,
                        color: AppColors.accent,
                      ),
                    ],
                  ],
                ),
                if (metaText != null)
                  Padding(
                    key: savedLabel != null
                        ? ValueKey('expense-row-saved-${expense.id}')
                        : null,
                    padding: const EdgeInsets.only(top: 3),
                    child: Row(
                      children: [
                        Icon(
                          savedLabel != null
                              ? Icons.schedule_outlined
                              : Icons.storefront_outlined,
                          size: 12,
                          color: AppColors.textMuted,
                        ),
                        const SizedBox(width: AppSpacing.xs),
                        Flexible(
                          child: Text(
                            metaText,
                            style: AppText.caption
                                .copyWith(color: AppColors.textMuted),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ),
          // Star toggle — pin an individual expense so it feeds the Money
          // "★ Starred only" subtotal. Hidden on read-only nested rows.
          if (onToggleFavorite != null) ...[
            const SizedBox(width: AppSpacing.xs),
            GestureDetector(
              onTap: onToggleFavorite,
              behavior: HitTestBehavior.opaque,
              child: Padding(
                padding: const EdgeInsets.all(AppSpacing.xs),
                child: Icon(
                  expense.isFavorite
                      ? Icons.star_rounded
                      : Icons.star_outline_rounded,
                  color: expense.isFavorite
                      ? AppColors.warn
                      : AppColors.textMuted,
                  size: 20,
                  semanticLabel: expense.isFavorite
                      ? 'Unfavorite expense'
                      : 'Favorite expense',
                ),
              ),
            ),
          ],
          const SizedBox(width: AppSpacing.md),
          // Amount + sync badge — the row's prominent figure.
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                fmtMoney(expense.currency, expense.amount),
                style: AppText.title.copyWith(
                  color: AppColors.textPrimary,
                  fontWeight: FontWeight.w700,
                ),
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

  /// Composes the muted metadata line: vendor (when distinct from the
  /// description) and the "Saved …" timestamp, joined with a thin dot. Returns
  /// null when there's nothing to show so the row stays a clean single line.
  ///
  /// The leading icon in [_buildContent] keys off whether [savedLabel] is set
  /// (clock when present, storefront otherwise) and the row carries the
  /// `expense-row-saved-{id}` key only when a saved label is rendered — keeping
  /// the saved-time test surface intact.
  String? _metaText(String vendor, String desc, String? savedLabel) {
    final parts = <String>[];
    if (vendor.isNotEmpty && vendor != desc) parts.add(vendor);
    if (savedLabel != null) parts.add('Saved $savedLabel');
    if (parts.isEmpty) return null;
    return parts.join('  ·  ');
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
