import 'package:flutter/material.dart';

import '../../repositories/activity_repository.dart';
import '../../ui/ui.dart';

// ── Status semantics ───────────────────────────────────────────────────────

Color activityStatusColor(AgentTask t) {
  if (t.isRunning) return AppColors.info;
  if (t.isFailed) return AppColors.error;
  if (t.isCancelled) return AppColors.textMuted;
  return AppColors.success;
}

String activityStatusLabel(AgentTask t) {
  if (t.isRunning) return 'running';
  if (t.isFailed) return 'failed';
  if (t.isCancelled) return 'cancelled';
  return 'done';
}

/// Trailing time string — live elapsed for running tasks, duration or a
/// relative completion time for settled ones.
String activityTimeLabel(AgentTask t) {
  if (t.isRunning && t.elapsedS != null) return '${t.elapsedS!.round()}s';
  if (t.durationS != null) return '${t.durationS!.toStringAsFixed(1)}s';
  final iso = t.completedAt ?? t.createdAt;
  return iso != null ? relativeTime(iso) : '';
}

String relativeTime(String iso) {
  if (iso.isEmpty) return '';
  final dt = DateTime.tryParse(iso)?.toLocal();
  if (dt == null) return iso;
  final diff = DateTime.now().difference(dt);
  if (diff.inSeconds < 60) return 'just now';
  if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
  if (diff.inHours < 24) return '${diff.inHours}h ago';
  if (diff.inDays < 7) return '${diff.inDays}d ago';
  return '${dt.day}/${dt.month}/${dt.year}';
}

// ── Row ────────────────────────────────────────────────────────────────────

/// A single agent task row — status dot, name, lane badge, a one-line subtitle
/// (current tool / phase while running, result preview once settled) and the
/// elapsed / duration on the right. Tapping opens the detail sheet; running
/// rows additionally show a stop button when [onCancel] is provided.
class ActivityRow extends StatelessWidget {
  const ActivityRow({
    super.key,
    required this.task,
    required this.onTap,
    this.onCancel,
  });

  final AgentTask task;
  final VoidCallback onTap;

  /// Fires `POST /api/agents/cancel` for this task. Only rendered while the
  /// task is running.
  final VoidCallback? onCancel;

  @override
  Widget build(BuildContext context) {
    final color = activityStatusColor(task);
    final subtitle = task.subtitle;

    return LzCard(
      padding: const EdgeInsets.all(AppSpacing.md),
      onTap: onTap,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 3),
            child: LzStatusDot(color: color, size: 9, glow: task.isRunning),
          ),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        task.name,
                        style: AppText.label
                            .copyWith(color: AppColors.textPrimary),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    const SizedBox(width: AppSpacing.sm),
                    LzBadge(label: activityStatusLabel(task), color: color),
                    const SizedBox(width: AppSpacing.sm),
                    Text(
                      activityTimeLabel(task),
                      style:
                          AppText.caption.copyWith(color: AppColors.textMuted),
                    ),
                  ],
                ),
                if (subtitle != null && subtitle.isNotEmpty) ...[
                  const SizedBox(height: AppSpacing.xs),
                  Text(
                    subtitle,
                    style: AppText.caption
                        .copyWith(color: AppColors.textSecondary),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
                if (task.lane.isNotEmpty) ...[
                  const SizedBox(height: AppSpacing.xs),
                  Text(
                    task.lane,
                    style: AppText.caption
                        .copyWith(color: AppColors.textMuted, fontSize: 10.5),
                  ),
                ],
              ],
            ),
          ),
          if (task.isRunning && onCancel != null) ...[
            const SizedBox(width: AppSpacing.sm),
            LzIconButton(
              icon: Icons.stop_rounded,
              color: AppColors.error,
              filled: true,
              tooltip: 'Cancel task',
              onPressed: onCancel,
            ),
          ],
        ],
      ),
    );
  }
}
