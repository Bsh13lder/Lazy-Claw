import 'package:flutter/material.dart';
import '../tokens/tokens.dart';
import 'lz_button.dart';

/// Token-styled dialogs.
///
/// - [LzDialog.show] presents arbitrary [content] with a [title] and custom
///   [actions].
/// - [LzConfirm.show] is a ready-made yes/no confirmation that resolves to a
///   `bool` (true = confirmed). Set [danger] for destructive confirmations.
abstract final class LzDialog {
  LzDialog._();

  static Future<T?> show<T>(
    BuildContext context, {
    required String title,
    required Widget content,
    List<Widget> actions = const [],
    bool barrierDismissible = true,
  }) {
    return showDialog<T>(
      context: context,
      barrierDismissible: barrierDismissible,
      barrierColor: Colors.black.withValues(alpha: 0.6),
      builder: (ctx) => AlertDialog(
        backgroundColor: AppColors.bgSurfaceElevated,
        surfaceTintColor: Colors.transparent,
        shape: const RoundedRectangleBorder(borderRadius: AppRadii.rXl),
        title: Text(title, style: AppText.titleL),
        content: DefaultTextStyle.merge(style: AppText.body, child: content),
        actionsPadding: const EdgeInsets.fromLTRB(
          AppSpacing.lg,
          0,
          AppSpacing.lg,
          AppSpacing.lg,
        ),
        actions: actions.isEmpty ? null : actions,
      ),
    );
  }
}

/// A two-button confirmation dialog. Resolves to `true` when confirmed.
abstract final class LzConfirm {
  LzConfirm._();

  static Future<bool> show(
    BuildContext context, {
    required String title,
    String? message,
    String confirmLabel = 'Confirm',
    String cancelLabel = 'Cancel',
    bool danger = false,
  }) async {
    final result = await LzDialog.show<bool>(
      context,
      title: title,
      content: message == null
          ? const SizedBox.shrink()
          : Text(message, style: AppText.body.copyWith(
              color: AppColors.textSecondary)),
      actions: [
        LzButton.ghost(
          label: cancelLabel,
          onPressed: () => Navigator.of(context).pop(false),
        ),
        const SizedBox(width: AppSpacing.sm),
        if (danger)
          LzButton.danger(
            label: confirmLabel,
            onPressed: () => Navigator.of(context).pop(true),
          )
        else
          LzButton.primary(
            label: confirmLabel,
            onPressed: () => Navigator.of(context).pop(true),
          ),
      ],
    );
    return result ?? false;
  }
}
