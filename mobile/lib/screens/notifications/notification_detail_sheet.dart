/// Bottom detail sheet for a Notification Center row.
///
/// Rows without a specific deep-link target used to mark-read and do nothing
/// visible on tap — the body is ellipsized at 2 lines in the list, so the
/// full message was unreachable. This sheet shows the complete notification:
/// title, full selectable body, kind + severity accents and the timestamp.
/// Presented via the kit's [LzBottomSheet]; all styling from design tokens.
library;

import 'package:flutter/material.dart';

import '../../repositories/notifications_repository.dart';
import '../../ui/ui.dart';

/// Opens the detail sheet for [n]. [accent] and [icon] come from the caller's
/// severity/kind mapping so the sheet matches the row it was opened from.
Future<void> showNotificationDetailSheet(
  BuildContext context,
  ServerNotification n, {
  required Color accent,
  required IconData icon,
}) {
  return LzBottomSheet.show<void>(
    context,
    builder: (context) => _NotificationDetailContent(
      n: n,
      accent: accent,
      icon: icon,
    ),
  );
}

class _NotificationDetailContent extends StatelessWidget {
  const _NotificationDetailContent({
    required this.n,
    required this.accent,
    required this.icon,
  });

  final ServerNotification n;
  final Color accent;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 34,
              height: 34,
              decoration: BoxDecoration(
                color: accent.withValues(alpha: 0.14),
                borderRadius: AppRadii.rMd,
              ),
              child: Icon(icon, size: 18, color: accent),
            ),
            const SizedBox(width: AppSpacing.md),
            Expanded(
              child: Text(
                n.title.isNotEmpty ? n.title : 'Notification',
                style: AppText.title.copyWith(color: AppColors.textPrimary),
              ),
            ),
          ],
        ),
        const SizedBox(height: AppSpacing.sm),
        Row(
          children: [
            if (n.kind.isNotEmpty) ...[
              LzChip(label: n.kind, color: accent),
              const SizedBox(width: AppSpacing.sm),
            ],
            if (n.repeatCount > 1) ...[
              LzChip(label: '×${n.repeatCount}', color: AppColors.textMuted),
              const SizedBox(width: AppSpacing.sm),
            ],
            Expanded(
              child: Text(
                n.createdAt,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                textAlign: TextAlign.end,
                style: AppText.caption.copyWith(color: AppColors.textMuted),
              ),
            ),
          ],
        ),
        if (n.body.isNotEmpty) ...[
          const SizedBox(height: AppSpacing.md),
          Flexible(
            child: SingleChildScrollView(
              child: SelectableText(
                n.body,
                style: AppText.body.copyWith(color: AppColors.textSecondary),
              ),
            ),
          ),
        ] else ...[
          const SizedBox(height: AppSpacing.md),
          Text(
            'No further detail — this is the whole message.',
            style: AppText.caption.copyWith(color: AppColors.textMuted),
          ),
        ],
        const SizedBox(height: AppSpacing.lg),
      ],
    );
  }
}
