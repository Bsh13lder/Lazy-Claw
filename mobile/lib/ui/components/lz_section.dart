import 'package:flutter/material.dart';
import '../tokens/tokens.dart';

/// A labeled content block: an uppercase [title] header with an optional
/// trailing action (e.g. "See all →") above [child].
///
/// Matches the §2.1 Home dashboard sections ("TODAY", "THIS MONTH", …).
class LzSection extends StatelessWidget {
  const LzSection({
    super.key,
    required this.title,
    required this.child,
    this.action,
    this.padding = EdgeInsets.zero,
  });

  final String title;
  final Widget child;

  /// Optional trailing widget aligned with the header (e.g. a "See all" button).
  final Widget? action;
  final EdgeInsetsGeometry padding;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: padding,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.only(bottom: AppSpacing.md),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    title.toUpperCase(),
                    style: AppText.caption.copyWith(
                      color: AppColors.textMuted,
                      letterSpacing: 0.8,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
                ?action,
              ],
            ),
          ),
          child,
        ],
      ),
    );
  }
}
