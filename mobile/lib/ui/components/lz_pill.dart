import 'package:flutter/material.dart';
import '../tokens/tokens.dart';
import 'lz_status_dot.dart';

/// A compact status pill: an optional leading [dotColor] dot + a [label] in a
/// rounded tonal container. Lighter-weight than [LzChip] — for read-only status
/// readouts (e.g. "Synced", "Connected", a sync state in the Home header).
class LzPill extends StatelessWidget {
  const LzPill({
    super.key,
    required this.label,
    this.dotColor,
    this.icon,
    this.color,
    this.glowDot = false,
  });

  final String label;

  /// If set, a leading [LzStatusDot] in this color.
  final Color? dotColor;

  /// Leading icon (alternative to [dotColor]).
  final IconData? icon;

  /// Text/icon foreground color (defaults to [AppColors.textSecondary]).
  final Color? color;
  final bool glowDot;

  @override
  Widget build(BuildContext context) {
    final fg = color ?? AppColors.textSecondary;
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.xs + 1,
      ),
      decoration: BoxDecoration(
        color: AppColors.bgSurfaceElevated,
        borderRadius: AppRadii.rPill,
        border: Border.all(color: AppColors.borderSubtle),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (dotColor != null) ...[
            LzStatusDot(color: dotColor!, glow: glowDot),
            const SizedBox(width: AppSpacing.sm),
          ] else if (icon != null) ...[
            Icon(icon, size: 14, color: fg),
            const SizedBox(width: AppSpacing.xs + 2),
          ],
          Text(
            label,
            style: AppText.caption.copyWith(
              color: fg,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}
