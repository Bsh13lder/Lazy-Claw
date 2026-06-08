import 'package:flutter/material.dart';
import '../tokens/tokens.dart';

/// A small colored status dot, optionally with a soft outer glow (for "live"
/// states). Reuse the semantic tokens via the named constructors.
class LzStatusDot extends StatelessWidget {
  const LzStatusDot({
    super.key,
    required this.color,
    this.size = 8,
    this.glow = false,
  });

  final Color color;
  final double size;

  /// Draw a soft halo around the dot (for active / connected states).
  final bool glow;

  const LzStatusDot.success({super.key, this.size = 8, this.glow = false})
      : color = AppColors.success;
  const LzStatusDot.warn({super.key, this.size = 8, this.glow = false})
      : color = AppColors.warn;
  const LzStatusDot.error({super.key, this.size = 8, this.glow = false})
      : color = AppColors.error;
  const LzStatusDot.muted({super.key, this.size = 8, this.glow = false})
      : color = AppColors.textMuted;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: color,
        shape: BoxShape.circle,
        boxShadow: glow
            ? [
                BoxShadow(
                  color: color.withValues(alpha: 0.5),
                  blurRadius: size,
                  spreadRadius: size / 3,
                ),
              ]
            : null,
      ),
    );
  }
}
