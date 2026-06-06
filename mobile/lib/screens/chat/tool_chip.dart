/// Polished tool-activity chip for the Chat screen.
///
/// Shows the tool name with a running/done icon. When done, tapping expands
/// an inline result preview. All styling from design tokens.
library;

import 'package:flutter/material.dart';
import '../../chat/chat_message.dart';
import '../../ui/ui.dart';

class ToolChip extends StatefulWidget {
  const ToolChip({super.key, required this.activity});
  final ToolActivity activity;

  @override
  State<ToolChip> createState() => _ToolChipState();
}

class _ToolChipState extends State<ToolChip> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final t = widget.activity;
    final isDone = t.resultPreview != null;

    final bgColor = isDone
        ? AppColors.success.withValues(alpha: 0.12)
        : AppColors.bgSurfaceElevated;
    final fgColor = isDone ? AppColors.success : AppColors.textMuted;
    final borderColor = isDone
        ? AppColors.success.withValues(alpha: 0.30)
        : AppColors.borderSubtle;

    return GestureDetector(
      onTap: isDone ? () => setState(() => _expanded = !_expanded) : null,
      child: AnimatedContainer(
        duration: AppMotion.fast,
        curve: AppMotion.curve,
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.sm + 2,
          vertical: AppSpacing.xs + 1,
        ),
        decoration: BoxDecoration(
          color: bgColor,
          borderRadius: AppRadii.rPill,
          border: Border.all(color: borderColor),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                // Running spinner vs done check
                if (!isDone)
                  SizedBox(
                    width: 11,
                    height: 11,
                    child: CircularProgressIndicator(
                      strokeWidth: 1.5,
                      color: fgColor,
                    ),
                  )
                else
                  Icon(Icons.check_circle_outline, size: 12, color: fgColor),
                const SizedBox(width: 5),
                Text(
                  t.name,
                  style: AppText.caption.copyWith(
                    color: fgColor,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                if (isDone && t.resultPreview!.isNotEmpty) ...[
                  const SizedBox(width: 3),
                  Icon(
                    _expanded ? Icons.expand_less : Icons.expand_more,
                    size: 12,
                    color: fgColor,
                  ),
                ],
              ],
            ),
            if (_expanded &&
                t.resultPreview != null &&
                t.resultPreview!.isNotEmpty) ...[
              const SizedBox(height: AppSpacing.xs),
              Text(
                t.resultPreview!,
                style: AppText.caption.copyWith(
                  color: AppColors.textSecondary,
                  fontWeight: FontWeight.w400,
                ),
                maxLines: 5,
                overflow: TextOverflow.ellipsis,
              ),
            ],
          ],
        ),
      ),
    );
  }
}
