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

  /// [actions] are pre-built widgets. [actionsBuilder] instead receives the
  /// dialog's OWN build context — use it when the actions must pop the dialog
  /// (`Navigator.of(dialogContext).pop(...)`). Popping via the caller's context
  /// is a bug when the dialog and the caller live on different navigators (e.g.
  /// a confirm popup — root navigator — shown over a modal bottom sheet — nested
  /// navigator): the caller's context resolves to the wrong navigator and pops
  /// the underlying sheet, leaving the popup frozen. [actionsBuilder] wins when
  /// both are supplied.
  static Future<T?> show<T>(
    BuildContext context, {
    required String title,
    required Widget content,
    List<Widget> actions = const [],
    List<Widget> Function(BuildContext dialogContext)? actionsBuilder,
    bool barrierDismissible = true,
  }) {
    return showDialog<T>(
      context: context,
      barrierDismissible: barrierDismissible,
      barrierColor: Colors.black.withValues(alpha: 0.6),
      builder: (ctx) {
        final resolvedActions = actionsBuilder?.call(ctx) ?? actions;
        return AlertDialog(
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
          actions: resolvedActions.isEmpty ? null : resolvedActions,
        );
      },
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
      // Pop via the dialog's OWN context, not the caller's — otherwise a confirm
      // shown over a modal bottom sheet resolves to the sheet's (nested)
      // navigator and pops the sheet instead of the popup, hanging this future
      // and freezing the popup on screen.
      actionsBuilder: (dialogContext) => [
        LzButton.ghost(
          label: cancelLabel,
          onPressed: () => Navigator.of(dialogContext).pop(false),
        ),
        const SizedBox(width: AppSpacing.sm),
        if (danger)
          LzButton.danger(
            label: confirmLabel,
            onPressed: () => Navigator.of(dialogContext).pop(true),
          )
        else
          LzButton.primary(
            label: confirmLabel,
            onPressed: () => Navigator.of(dialogContext).pop(true),
          ),
      ],
    );
    return result ?? false;
  }
}
