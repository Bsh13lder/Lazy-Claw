/// Background-task result card for the Chat screen.
///
/// Rendered for messages with role == 'bg_task'. Uses [LzCard] + design tokens
/// for the success/failure treatment. When the reducer captured a live
/// activity timeline for the task, the card grows an expandable
/// "Activity (N)" log.
library;

import 'package:flutter/material.dart';
import '../../chat/chat_message.dart';
import '../../ui/ui.dart';
import 'activity_timeline.dart';

class BgTaskCard extends StatefulWidget {
  const BgTaskCard(this.result, {super.key});
  final BackgroundTaskResult result;

  @override
  State<BgTaskCard> createState() => _BgTaskCardState();
}

class _BgTaskCardState extends State<BgTaskCard> {
  bool _logExpanded = false;

  @override
  Widget build(BuildContext context) {
    final result = widget.result;
    // The result text already arrived as the consolidated assistant reply —
    // render header-only (name + status icon + duration) so the user sees the
    // task settled without the same wall of text twice.
    final headerOnly = result.duplicateOfReply;
    final success = result.success;
    final accentColor = success ? AppColors.success : AppColors.error;
    final bgColor = accentColor.withValues(alpha: 0.10);
    final borderColor = accentColor.withValues(alpha: 0.25);
    final icon = success ? Icons.check_circle_outline : Icons.error_outline;

    return Align(
      alignment: Alignment.centerLeft,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.xs),
        child: ConstrainedBox(
          constraints: BoxConstraints(
            maxWidth: MediaQuery.of(context).size.width * 0.86,
          ),
          child: LzCard(
            color: bgColor,
            borderColor: borderColor,
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.lg,
              vertical: AppSpacing.md,
            ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(icon, size: 18, color: accentColor),
                const SizedBox(width: AppSpacing.sm),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        result.name,
                        style: AppText.label.copyWith(color: accentColor),
                        overflow: TextOverflow.ellipsis,
                      ),
                      if (!headerOnly &&
                          result.detail != null &&
                          result.detail!.isNotEmpty) ...[
                        const SizedBox(height: AppSpacing.xs),
                        Text(
                          result.detail!,
                          style: AppText.caption.copyWith(
                            color: AppColors.textSecondary,
                            fontWeight: FontWeight.w400,
                          ),
                          maxLines: 3,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ],
                      if (result.durationMs != null) ...[
                        const SizedBox(height: AppSpacing.xs),
                        Text(
                          '${(result.durationMs! / 1000).toStringAsFixed(1)}s',
                          style: AppText.caption.copyWith(
                            color: accentColor.withValues(alpha: 0.65),
                          ),
                        ),
                      ],
                      if (!headerOnly && result.events.isNotEmpty) ...[
                        const SizedBox(height: AppSpacing.sm),
                        _activityToggle(),
                        if (_logExpanded) ...[
                          const SizedBox(height: AppSpacing.xs),
                          ActivityEventList(events: result.events),
                        ],
                      ],
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _activityToggle() {
    final n = widget.result.events.length;
    return GestureDetector(
      onTap: () => setState(() => _logExpanded = !_logExpanded),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(
            Icons.timeline_outlined,
            size: 12,
            color: AppColors.textMuted,
          ),
          const SizedBox(width: AppSpacing.xs),
          Text(
            'Activity ($n)',
            style: AppText.caption.copyWith(
              color: AppColors.textMuted,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(width: AppSpacing.xs),
          Icon(
            _logExpanded ? Icons.expand_less : Icons.expand_more,
            size: 13,
            color: AppColors.textMuted,
          ),
        ],
      ),
    );
  }
}
