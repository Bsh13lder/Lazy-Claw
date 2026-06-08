import 'package:flutter/material.dart';
import '../tokens/tokens.dart';

/// An elevated surface card: [AppColors.bgSurface] fill, a 1px
/// [AppColors.borderSubtle] hairline, [AppRadii.lg] corners, and the standard
/// [AppElevation.card] shadow — the canonical container for content blocks.
///
/// Pass [onTap] to make the whole card tappable (with an ink ripple). Use
/// [padding] to override the default [AppSpacing.card]; pass [EdgeInsets.zero]
/// when the child manages its own insets (e.g. a list).
class LzCard extends StatelessWidget {
  const LzCard({
    super.key,
    required this.child,
    this.padding = AppSpacing.card,
    this.onTap,
    this.color,
    this.borderColor,
    this.elevated = true,
    this.gradient,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final VoidCallback? onTap;

  /// Override the fill color (defaults to [AppColors.bgSurface]).
  final Color? color;

  /// Override the hairline border color (defaults to [AppColors.borderSubtle]).
  final Color? borderColor;

  /// Whether to draw the drop shadow (defaults to true).
  final bool elevated;

  /// Optional gradient fill (overrides [color] for the background paint).
  final Gradient? gradient;

  @override
  Widget build(BuildContext context) {
    final decoration = BoxDecoration(
      color: gradient == null ? (color ?? AppColors.bgSurface) : null,
      gradient: gradient,
      borderRadius: AppRadii.rLg,
      border: Border.all(color: borderColor ?? AppColors.borderSubtle),
      boxShadow: elevated ? AppElevation.card : AppElevation.none,
    );

    final content = Padding(padding: padding, child: child);

    if (onTap == null) {
      return DecoratedBox(decoration: decoration, child: content);
    }

    return Material(
      color: Colors.transparent,
      borderRadius: AppRadii.rLg,
      child: Ink(
        decoration: decoration,
        child: InkWell(
          onTap: onTap,
          borderRadius: AppRadii.rLg,
          splashColor: AppColors.accent.withValues(alpha: 0.06),
          highlightColor: AppColors.bgSurfaceHover,
          child: content,
        ),
      ),
    );
  }
}
