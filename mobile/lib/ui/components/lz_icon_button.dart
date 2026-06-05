import 'package:flutter/material.dart';
import '../tokens/tokens.dart';

/// A compact, square icon button with a circular ripple. Optionally [filled]
/// (tonal surface chip) or [accent] (amber tint).
///
/// Use for app-bar actions, card affordances, and toolbars.
class LzIconButton extends StatelessWidget {
  const LzIconButton({
    super.key,
    required this.icon,
    required this.onPressed,
    this.tooltip,
    this.filled = false,
    this.accent = false,
    this.size = 22,
    this.color,
  });

  final IconData icon;
  final VoidCallback? onPressed;
  final String? tooltip;

  /// Draw a tonal background chip behind the icon.
  final bool filled;

  /// Tint the icon (and chip) with the amber accent.
  final bool accent;
  final double size;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final fg = color ??
        (accent ? AppColors.accent : AppColors.textSecondary);

    Widget button = InkResponse(
      onTap: onPressed,
      radius: 24,
      splashColor: AppColors.accent.withValues(alpha: 0.12),
      highlightColor: AppColors.bgSurfaceHover,
      child: Container(
        width: 40,
        height: 40,
        alignment: Alignment.center,
        decoration: filled
            ? BoxDecoration(
                color: accent
                    ? AppColors.accent.withValues(alpha: 0.14)
                    : AppColors.bgSurfaceElevated,
                shape: BoxShape.circle,
                border: Border.all(color: AppColors.borderSubtle),
              )
            : null,
        child: Icon(icon, size: size, color: fg),
      ),
    );

    if (tooltip != null) {
      button = Tooltip(message: tooltip!, child: button);
    }
    return button;
  }
}
