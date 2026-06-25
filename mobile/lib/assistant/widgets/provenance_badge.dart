/// Per-turn provenance badge — tells the user, at a glance, whether the current
/// "Hey Lazy" reply stayed on the phone or went to the cloud.
///
/// Dual-encoded (colour + icon + label) so it never relies on colour alone:
/// emerald "On-device" vs amber "Cloud". Wrapped in [Semantics] so a screen
/// reader announces the full provenance.
library;

import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';

import '../../ui/ui.dart';
import '../lazy_assistant_controller.dart';

class ProvenanceBadge extends StatelessWidget {
  const ProvenanceBadge({super.key, required this.source});

  final TurnSource source;

  @override
  Widget build(BuildContext context) {
    final isCloud = source == TurnSource.cloud;
    final color = isCloud ? AppColors.warn : AppColors.accent;
    final label = isCloud ? 'Cloud' : 'On-device';
    final semantics = isCloud ? 'Processed in the cloud' : 'Processed on device';
    final icon = isCloud ? LucideIcons.cloud : LucideIcons.smartphone;

    return Semantics(
      label: semantics,
      container: true,
      excludeSemantics: true,
      child: Container(
        padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.sm, vertical: AppSpacing.xs),
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.14),
          borderRadius: AppRadii.rPill,
          border: Border.all(color: color.withValues(alpha: 0.4)),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, color: color, size: 13),
            const SizedBox(width: AppSpacing.xs),
            Text(label, style: AppText.label.copyWith(color: color)),
          ],
        ),
      ),
    );
  }
}
