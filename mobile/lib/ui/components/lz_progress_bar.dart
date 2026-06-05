import 'package:flutter/material.dart';
import '../tokens/tokens.dart';

/// A rounded determinate progress bar. By default it paints amber; set
/// [trafficLight] to color the fill by usage (<70% success, 70–90% warn,
/// ≥90% error) — the budget-bar behavior from the spec.
///
/// [value] is clamped to 0..1. The fill animates between updates.
class LzProgressBar extends StatelessWidget {
  const LzProgressBar({
    super.key,
    required this.value,
    this.height = 8,
    this.trafficLight = false,
    this.color,
    this.trackColor,
  });

  final double value;
  final double height;

  /// Color the fill by [AppColors.trafficLight] thresholds (budget bars).
  final bool trafficLight;

  /// Explicit fill color (overrides [trafficLight] and the amber default).
  final Color? color;
  final Color? trackColor;

  @override
  Widget build(BuildContext context) {
    final clamped = value.isNaN ? 0.0 : value.clamp(0.0, 1.0);
    final fill = color ??
        (trafficLight ? AppColors.trafficLight(clamped) : AppColors.accent);

    return ClipRRect(
      borderRadius: AppRadii.rPill,
      child: Stack(
        children: [
          Container(
            height: height,
            color: trackColor ?? AppColors.bgSurfaceHover,
          ),
          FractionallySizedBox(
            widthFactor: clamped,
            child: AnimatedContainer(
              duration: AppMotion.base,
              curve: AppMotion.curve,
              height: height,
              decoration: BoxDecoration(
                color: fill,
                borderRadius: AppRadii.rPill,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
