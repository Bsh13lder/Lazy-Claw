/// Background-task result card for the Chat screen.
///
/// Rendered for messages with role == 'bg_task'. Uses [LzCard] + design tokens
/// for the success/failure treatment.
library;

import 'package:flutter/material.dart';
import '../../chat/chat_message.dart';
import '../../ui/ui.dart';

class BgTaskCard extends StatelessWidget {
  const BgTaskCard(this.result, {super.key});
  final BackgroundTaskResult result;

  @override
  Widget build(BuildContext context) {
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
                      if (result.detail != null &&
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
}
