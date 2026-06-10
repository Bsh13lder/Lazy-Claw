import 'package:flutter/material.dart';

import '../../repositories/activity_repository.dart';
import '../../ui/ui.dart';
import 'activity_row.dart';

/// Shows the full detail for one agent task in a token-styled bottom sheet:
/// status, lane, timing, the tools it used, and the full (decrypted) result or
/// error body.
Future<void> showActivityDetail(BuildContext context, AgentTask task) {
  return LzBottomSheet.show<void>(
    context,
    title: task.name,
    builder: (_) => _ActivityDetail(task: task),
  );
}

class _ActivityDetail extends StatelessWidget {
  const _ActivityDetail({required this.task});

  final AgentTask task;

  @override
  Widget build(BuildContext context) {
    final color = activityStatusColor(task);
    final body = (task.error != null && task.error!.isNotEmpty)
        ? task.error!
        : (task.result ?? task.resultPreview ?? '');

    return ConstrainedBox(
      constraints: BoxConstraints(
        maxHeight: MediaQuery.of(context).size.height * 0.7,
      ),
      child: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            // Status + lane + timing chips.
            Wrap(
              spacing: AppSpacing.sm,
              runSpacing: AppSpacing.sm,
              children: [
                LzBadge(label: activityStatusLabel(task), color: color),
                if (task.lane.isNotEmpty) LzChip(label: task.lane, dense: true),
                LzChip(label: activityTimeLabel(task), dense: true),
                if (task.projectTag != null)
                  LzChip(label: task.projectTag!, dense: true),
              ],
            ),

            // Tools used.
            if (task.recentTools.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.lg),
              Text('Tools used',
                  style: AppText.label.copyWith(color: AppColors.textPrimary)),
              const SizedBox(height: AppSpacing.sm),
              Wrap(
                spacing: AppSpacing.sm,
                runSpacing: AppSpacing.sm,
                children: task.recentTools
                    .map((t) => LzChip(label: t, dense: true))
                    .toList(),
              ),
            ],

            // Cost / tokens (background tasks only).
            if (task.costUsd != null || task.tokensUsed != null) ...[
              const SizedBox(height: AppSpacing.lg),
              Text(
                [
                  if (task.tokensUsed != null) '${task.tokensUsed} tokens',
                  if (task.costUsd != null)
                    '\$${task.costUsd!.toStringAsFixed(4)}',
                ].join('  ·  '),
                style: AppText.caption.copyWith(color: AppColors.textMuted),
              ),
            ],

            // Result / error body.
            if (body.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.lg),
              Text(
                task.isFailed ? 'Error' : 'Result',
                style: AppText.label.copyWith(color: AppColors.textPrimary),
              ),
              const SizedBox(height: AppSpacing.sm),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(AppSpacing.md),
                decoration: BoxDecoration(
                  color: AppColors.bgSurfaceElevated,
                  borderRadius: AppRadii.rMd,
                  border: Border.all(color: AppColors.borderSubtle),
                ),
                child: SelectableText(
                  body,
                  style: AppText.body.copyWith(
                    color: task.isFailed
                        ? AppColors.error
                        : AppColors.textSecondary,
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
