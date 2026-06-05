import 'package:flutter/material.dart';
import '../tokens/tokens.dart';

/// Helpers for showing a token-styled modal bottom sheet.
///
/// [LzBottomSheet.show] presents [builder]'s content inside a rounded surface
/// with a grab handle and an optional [title] header, keyboard-aware insets,
/// and the standard scrim. Returns whatever the sheet pops with.
abstract final class LzBottomSheet {
  LzBottomSheet._();

  static Future<T?> show<T>(
    BuildContext context, {
    required WidgetBuilder builder,
    String? title,
    bool isScrollControlled = true,
  }) {
    return showModalBottomSheet<T>(
      context: context,
      isScrollControlled: isScrollControlled,
      backgroundColor: AppColors.bgSurface,
      barrierColor: Colors.black.withValues(alpha: 0.55),
      shape: const RoundedRectangleBorder(
        borderRadius:
            BorderRadius.vertical(top: Radius.circular(AppRadii.xl)),
      ),
      builder: (ctx) => _SheetShell(title: title, child: builder(ctx)),
    );
  }
}

class _SheetShell extends StatelessWidget {
  const _SheetShell({required this.child, this.title});

  final Widget child;
  final String? title;

  @override
  Widget build(BuildContext context) {
    final bottomInset = MediaQuery.of(context).viewInsets.bottom;
    return Padding(
      padding: EdgeInsets.only(bottom: bottomInset),
      child: SafeArea(
        top: false,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const SizedBox(height: AppSpacing.md),
            // Grab handle.
            Container(
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                color: AppColors.borderDefault,
                borderRadius: AppRadii.rPill,
              ),
            ),
            if (title != null) ...[
              const SizedBox(height: AppSpacing.md),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xl),
                child: Row(
                  children: [
                    Expanded(child: Text(title!, style: AppText.titleL)),
                  ],
                ),
              ),
            ],
            const SizedBox(height: AppSpacing.lg),
            Flexible(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(
                  AppSpacing.xl,
                  0,
                  AppSpacing.xl,
                  AppSpacing.xl,
                ),
                child: child,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
