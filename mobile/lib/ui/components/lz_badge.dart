import 'package:flutter/material.dart';
import '../tokens/tokens.dart';

/// A small count / status badge. Renders a [count] (capped at 99+) or arbitrary
/// [label] text in a pill. Use [color] for semantic variants; defaults to the
/// amber accent. When [count] is 0 and [showZero] is false, renders nothing.
class LzBadge extends StatelessWidget {
  const LzBadge({
    super.key,
    this.count,
    this.label,
    this.color,
    this.showZero = false,
  }) : assert(count != null || label != null,
            'LzBadge needs a count or a label');

  final int? count;
  final String? label;
  final Color? color;
  final bool showZero;

  @override
  Widget build(BuildContext context) {
    final text = label ??
        (count! > 99 ? '99+' : '$count');

    if (label == null && count == 0 && !showZero) {
      return const SizedBox.shrink();
    }

    final c = color ?? AppColors.accent;
    return Container(
      constraints: const BoxConstraints(minWidth: 20),
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: c,
        borderRadius: AppRadii.rPill,
      ),
      child: Text(
        text,
        textAlign: TextAlign.center,
        style: AppText.caption.copyWith(
          color: AppColors.onAccent,
          fontWeight: FontWeight.w700,
          height: 1.1,
        ),
      ),
    );
  }
}
