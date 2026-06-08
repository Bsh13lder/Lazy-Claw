import 'package:flutter/material.dart';
import '../tokens/tokens.dart';

/// Severity variants for [LzBanner].
enum LzBannerVariant { offline, info, error, success, warn }

/// A full-width inline banner pinned above content. Replaces ad-hoc offline /
/// info / error strips across screens. Renders a leading icon, [message] text,
/// and an optional trailing [action]. Colors derive from the semantic tokens.
class LzBanner extends StatelessWidget {
  const LzBanner({
    super.key,
    required this.message,
    this.variant = LzBannerVariant.info,
    this.icon,
    this.action,
    this.safeAreaTop = true,
  });

  final String message;
  final LzBannerVariant variant;

  /// Override the leading icon (defaults per [variant]).
  final IconData? icon;
  final Widget? action;

  /// Pad for the top safe area (use when this banner sits under the status bar).
  final bool safeAreaTop;

  /// An offline-state banner (cloud-off, amber/warn).
  const LzBanner.offline({
    super.key,
    this.message = 'Computer offline — changes will sync',
    this.action,
    this.safeAreaTop = true,
  })  : variant = LzBannerVariant.offline,
        icon = null;

  const LzBanner.error({
    super.key,
    required this.message,
    this.action,
    this.safeAreaTop = true,
  })  : variant = LzBannerVariant.error,
        icon = null;

  const LzBanner.info({
    super.key,
    required this.message,
    this.action,
    this.safeAreaTop = true,
  })  : variant = LzBannerVariant.info,
        icon = null;

  /// A "local storage degraded" banner (warn): the device's local DB failed, so
  /// data may be incomplete. Offers a "Refresh" affordance ([onRetry]) to
  /// re-read the current store, and a "Reset" affordance ([onReset]) to rebuild
  /// it from scratch (the latter needs an app restart to re-open the file DB).
  factory LzBanner.degraded({
    Key? key,
    required VoidCallback onRetry,
    required VoidCallback onReset,
    bool safeAreaTop = true,
  }) {
    return LzBanner(
      key: key,
      message: 'Local storage unavailable — data may be incomplete.',
      variant: LzBannerVariant.warn,
      safeAreaTop: safeAreaTop,
      action: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          _BannerTapAction(
            label: 'Refresh',
            color: AppColors.warn,
            onTap: onRetry,
          ),
          const SizedBox(width: AppSpacing.xs),
          _BannerTapAction(
            label: 'Reset',
            color: AppColors.warn,
            onTap: onReset,
          ),
        ],
      ),
    );
  }

  _Style get _style {
    switch (variant) {
      case LzBannerVariant.offline:
        return const _Style(AppColors.warn, Icons.cloud_off_outlined);
      case LzBannerVariant.info:
        return const _Style(AppColors.info, Icons.info_outline);
      case LzBannerVariant.error:
        return const _Style(AppColors.error, Icons.error_outline);
      case LzBannerVariant.success:
        return const _Style(AppColors.success, Icons.check_circle_outline);
      case LzBannerVariant.warn:
        return const _Style(AppColors.warn, Icons.warning_amber_rounded);
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = _style;
    return Material(
      color: s.color.withValues(alpha: 0.14),
      child: SafeArea(
        top: safeAreaTop,
        bottom: false,
        child: Padding(
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.lg,
            vertical: AppSpacing.sm + 2,
          ),
          child: Row(
            children: [
              Icon(icon ?? s.icon, color: s.color, size: 18),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: Text(
                  message,
                  style: AppText.caption.copyWith(
                    color: s.color,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
              if (action != null) ...[
                const SizedBox(width: AppSpacing.sm),
                action!,
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _Style {
  final Color color;
  final IconData icon;
  const _Style(this.color, this.icon);
}

/// A compact, token-styled tappable text label used as a trailing affordance
/// inside a banner (e.g. the Refresh / Reset actions on [LzBanner.degraded]).
class _BannerTapAction extends StatelessWidget {
  const _BannerTapAction({
    required this.label,
    required this.onTap,
    required this.color,
  });

  final String label;
  final VoidCallback onTap;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: AppRadii.rSm,
      // Guarantee a comfortable (>=44dp) tap target without enlarging the text.
      child: ConstrainedBox(
        constraints: const BoxConstraints(minHeight: 44),
        child: Padding(
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.sm,
            vertical: AppSpacing.xs,
          ),
          child: Center(
            widthFactor: 1,
            child: Text(
              label,
              style: AppText.caption.copyWith(
                color: color,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
        ),
      ),
    );
  }
}
