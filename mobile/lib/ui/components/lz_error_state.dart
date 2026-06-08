import 'package:flutter/material.dart';
import '../tokens/tokens.dart';
import 'lz_button.dart';

/// A centered error placeholder: a muted [icon], an error [message], and a
/// primary "Retry" button wired to [onRetry].
///
/// Use this instead of leaving a screen on an infinite loading skeleton when a
/// fetch fails — it gives the user a clear message and a way to recover. Mirrors
/// [LzEmptyState]'s layout and token usage.
class LzErrorState extends StatelessWidget {
  const LzErrorState({
    super.key,
    required this.message,
    required this.onRetry,
    this.icon,
  });

  final String message;
  final VoidCallback onRetry;

  /// Override the leading icon (defaults to a cloud-off glyph).
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.xl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 72,
              height: 72,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: AppColors.bgSurfaceElevated,
                shape: BoxShape.circle,
                border: Border.all(color: AppColors.borderSubtle),
              ),
              child: Icon(
                icon ?? Icons.cloud_off_outlined,
                size: 34,
                color: AppColors.error,
              ),
            ),
            const SizedBox(height: AppSpacing.lg),
            Text(
              message,
              textAlign: TextAlign.center,
              style: AppText.body.copyWith(color: AppColors.textSecondary),
            ),
            const SizedBox(height: AppSpacing.xl),
            LzButton.primary(
              label: 'Retry',
              icon: Icons.refresh,
              onPressed: onRetry,
            ),
          ],
        ),
      ),
    );
  }
}
