import 'package:flutter/material.dart';
import '../tokens/tokens.dart';

/// A shimmering placeholder block used while content loads. Animates a soft
/// highlight sweep across a rounded surface. Compose several to mimic a card or
/// list row; or use [LzSkeleton.list] for a ready-made list placeholder.
class LzSkeleton extends StatefulWidget {
  const LzSkeleton({
    super.key,
    this.width,
    this.height = 16,
    this.borderRadius = AppRadii.rSm,
  });

  final double? width;
  final double height;
  final BorderRadius borderRadius;

  /// A column of [count] shimmering card rows (each a title + two lines).
  static Widget list({int count = 5, EdgeInsetsGeometry? padding}) {
    return _SkeletonList(count: count, padding: padding);
  }

  @override
  State<LzSkeleton> createState() => _LzSkeletonState();
}

class _LzSkeletonState extends State<LzSkeleton>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: AppMotion.shimmer,
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) {
        final t = _controller.value;
        return Container(
          width: widget.width,
          height: widget.height,
          decoration: BoxDecoration(
            borderRadius: widget.borderRadius,
            gradient: LinearGradient(
              begin: Alignment(-1 - 2 * (1 - t), 0),
              end: Alignment(1 - 2 * (1 - t) + 2, 0),
              colors: const [
                AppColors.bgSurfaceElevated,
                AppColors.bgSurfaceHover,
                AppColors.bgSurfaceElevated,
              ],
              stops: const [0.35, 0.5, 0.65],
            ),
          ),
        );
      },
    );
  }
}

class _SkeletonList extends StatelessWidget {
  const _SkeletonList({required this.count, this.padding});

  final int count;
  final EdgeInsetsGeometry? padding;

  @override
  Widget build(BuildContext context) {
    return ListView.separated(
      padding: padding ??
          const EdgeInsets.symmetric(
            horizontal: AppSpacing.lg,
            vertical: AppSpacing.md,
          ),
      itemCount: count,
      physics: const NeverScrollableScrollPhysics(),
      shrinkWrap: true,
      separatorBuilder: (_, index) => const SizedBox(height: AppSpacing.md),
      itemBuilder: (_, index) => Container(
        padding: const EdgeInsets.all(AppSpacing.lg),
        decoration: BoxDecoration(
          color: AppColors.bgSurface,
          borderRadius: AppRadii.rLg,
          border: Border.all(color: AppColors.borderSubtle),
        ),
        child: const Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            LzSkeleton(width: 160, height: 14),
            SizedBox(height: AppSpacing.md),
            LzSkeleton(height: 10),
            SizedBox(height: AppSpacing.sm),
            LzSkeleton(width: 220, height: 10),
          ],
        ),
      ),
    );
  }
}
