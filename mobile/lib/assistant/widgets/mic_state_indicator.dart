/// Tiny mic-state dot that lives in the mic affordance.
///
/// Dual-encoded so it never relies on colour alone: a FILLED emerald circle
/// means the mic is live (listening), a HOLLOW grey rounded-square means it is
/// muted/idle. Wrapped in [Semantics] + [Tooltip] for accessibility.
library;

import 'package:flutter/material.dart';

import '../../ui/ui.dart';

class MicStateIndicator extends StatelessWidget {
  const MicStateIndicator({super.key, required this.live, this.size = 12});

  /// True while the microphone is actively listening.
  final bool live;
  final double size;

  @override
  Widget build(BuildContext context) {
    final label = live ? 'Microphone live' : 'Microphone muted';
    return Semantics(
      label: label,
      child: Tooltip(
        message: label,
        child: SizedBox(
          width: size,
          height: size,
          child: live
              // Live: filled emerald circle.
              ? DecoratedBox(
                  decoration: const BoxDecoration(
                    shape: BoxShape.circle,
                    color: AppColors.accent,
                  ),
                )
              // Muted: hollow grey rounded-square (different shape, not just hue).
              : DecoratedBox(
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(3),
                    border: Border.all(color: AppColors.textMuted, width: 1.5),
                  ),
                ),
        ),
      ),
    );
  }
}
