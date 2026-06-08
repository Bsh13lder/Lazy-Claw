import 'package:flutter/material.dart';
import '../tokens/tokens.dart';

/// Visual variants for [LzButton].
enum LzButtonVariant {
  /// Filled amber — the single primary action on a surface.
  primary,

  /// Tonal — a muted surface fill for secondary actions.
  secondary,

  /// Text-only, no background.
  ghost,

  /// Filled red — destructive actions.
  danger,
}

/// The app's button. One widget, four [LzButtonVariant]s, consistent sizing.
///
/// Supports a leading [icon], a [loading] spinner (disables interaction), and
/// [expand] to fill the available width. All colors/spacing come from tokens.
class LzButton extends StatelessWidget {
  const LzButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.variant = LzButtonVariant.primary,
    this.icon,
    this.loading = false,
    this.expand = false,
  });

  final String label;
  final VoidCallback? onPressed;
  final LzButtonVariant variant;
  final IconData? icon;
  final bool loading;
  final bool expand;

  /// Convenience constructors.
  const LzButton.primary({
    super.key,
    required this.label,
    required this.onPressed,
    this.icon,
    this.loading = false,
    this.expand = false,
  }) : variant = LzButtonVariant.primary;

  const LzButton.secondary({
    super.key,
    required this.label,
    required this.onPressed,
    this.icon,
    this.loading = false,
    this.expand = false,
  }) : variant = LzButtonVariant.secondary;

  const LzButton.ghost({
    super.key,
    required this.label,
    required this.onPressed,
    this.icon,
    this.loading = false,
    this.expand = false,
  }) : variant = LzButtonVariant.ghost;

  const LzButton.danger({
    super.key,
    required this.label,
    required this.onPressed,
    this.icon,
    this.loading = false,
    this.expand = false,
  }) : variant = LzButtonVariant.danger;

  _Palette get _palette {
    switch (variant) {
      case LzButtonVariant.primary:
        return const _Palette(
          bg: AppColors.accent,
          fg: AppColors.onAccent,
          border: null,
        );
      case LzButtonVariant.secondary:
        return const _Palette(
          bg: AppColors.bgSurfaceElevated,
          fg: AppColors.textPrimary,
          border: AppColors.borderDefault,
        );
      case LzButtonVariant.ghost:
        return const _Palette(
          bg: Colors.transparent,
          fg: AppColors.accent,
          border: null,
        );
      case LzButtonVariant.danger:
        return const _Palette(
          bg: AppColors.error,
          fg: AppColors.onAccent,
          border: null,
        );
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = _palette;
    final enabled = onPressed != null && !loading;

    final child = loading
        ? SizedBox(
            height: 18,
            width: 18,
            child: CircularProgressIndicator(strokeWidth: 2, color: p.fg),
          )
        : Row(
            mainAxisSize: expand ? MainAxisSize.max : MainAxisSize.min,
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              if (icon != null) ...[
                Icon(icon, size: 18, color: p.fg),
                const SizedBox(width: AppSpacing.sm),
              ],
              Flexible(
                child: Text(
                  label,
                  style: AppText.label.copyWith(color: p.fg),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          );

    final button = AnimatedOpacity(
      duration: AppMotion.fast,
      opacity: enabled ? 1 : 0.45,
      child: Material(
        color: p.bg,
        borderRadius: AppRadii.rMd,
        child: InkWell(
          onTap: enabled ? onPressed : null,
          borderRadius: AppRadii.rMd,
          splashColor: AppColors.onAccent.withValues(alpha: 0.08),
          child: Container(
            height: 48,
            padding:
                const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
            alignment: Alignment.center,
            decoration: BoxDecoration(
              borderRadius: AppRadii.rMd,
              border: p.border != null
                  ? Border.all(color: p.border!)
                  : null,
            ),
            child: child,
          ),
        ),
      ),
    );

    return expand ? SizedBox(width: double.infinity, child: button) : button;
  }
}

class _Palette {
  final Color bg;
  final Color fg;
  final Color? border;
  const _Palette({required this.bg, required this.fg, required this.border});
}
