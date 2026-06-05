import 'package:flutter/material.dart';
import '../tokens/tokens.dart';

/// A themed pull-to-refresh wrapper. Thin shell over [RefreshIndicator] that
/// applies the design-system colors so every scroll surface refreshes with the
/// same amber spinner on the elevated surface background.
class LzRefresh extends StatelessWidget {
  const LzRefresh({
    super.key,
    required this.onRefresh,
    required this.child,
  });

  final Future<void> Function() onRefresh;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      onRefresh: onRefresh,
      color: AppColors.accent,
      backgroundColor: AppColors.bgSurfaceElevated,
      strokeWidth: 2.5,
      displacement: 36,
      child: child,
    );
  }
}
