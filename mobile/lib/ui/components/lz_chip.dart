import 'package:flutter/material.dart';
import '../tokens/tokens.dart';

/// A filter / choice chip. When [selected] it fills with a tinted [color]
/// (defaults to the amber accent); otherwise it's a subtle outlined surface.
///
/// Pass [onTap] for an interactive (toggleable) chip, or leave it null for a
/// static label chip (e.g. a priority or tag marker).
class LzChip extends StatelessWidget {
  const LzChip({
    super.key,
    required this.label,
    this.selected = false,
    this.onTap,
    this.icon,
    this.color,
    this.dense = false,
  });

  final String label;
  final bool selected;
  final VoidCallback? onTap;
  final IconData? icon;

  /// Accent color used when [selected] (defaults to [AppColors.accent]).
  final Color? color;
  final bool dense;

  @override
  Widget build(BuildContext context) {
    final accent = color ?? AppColors.accent;
    final bg = selected
        ? accent.withValues(alpha: 0.16)
        : AppColors.bgSurfaceElevated;
    final border = selected ? accent.withValues(alpha: 0.5) : AppColors.borderDefault;
    final fg = selected ? accent : AppColors.textSecondary;

    final content = Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (icon != null) ...[
          Icon(icon, size: dense ? 13 : 15, color: fg),
          const SizedBox(width: AppSpacing.xs),
        ],
        Text(
          label,
          style: AppText.caption.copyWith(
            color: fg,
            fontWeight: selected ? FontWeight.w700 : FontWeight.w600,
          ),
        ),
      ],
    );

    final chip = Container(
      padding: EdgeInsets.symmetric(
        horizontal: dense ? AppSpacing.sm : AppSpacing.md,
        vertical: dense ? 3 : AppSpacing.xs + 1,
      ),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: AppRadii.rPill,
        border: Border.all(color: border),
      ),
      child: content,
    );

    if (onTap == null) return chip;
    return Material(
      color: Colors.transparent,
      borderRadius: AppRadii.rPill,
      child: InkWell(
        onTap: onTap,
        borderRadius: AppRadii.rPill,
        child: chip,
      ),
    );
  }
}
