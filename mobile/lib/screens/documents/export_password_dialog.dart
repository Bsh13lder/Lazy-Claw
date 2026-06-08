import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

/// Prompt for an OPTIONAL export password.
///
/// Returns:
/// - `null` → the user cancelled (don't export)
/// - `''`   → export the plain file (no encryption)
/// - `'pw'` → export wrapped in an AES-256 encrypted `.zip` with this password
Future<String?> promptExportPassword(BuildContext context) {
  final ctrl = TextEditingController();
  return LzDialog.show<String>(
    context,
    title: 'Export',
    content: Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Set a password to download an AES-256 encrypted .zip, '
          'or leave it blank for a plain file.',
          style: AppText.caption,
        ),
        const SizedBox(height: AppSpacing.sm),
        LzTextField(
          controller: ctrl,
          label: 'Password (optional)',
          obscureText: true,
          autofocus: true,
          textInputAction: TextInputAction.done,
          onSubmitted: (_) => Navigator.of(context).pop(ctrl.text),
        ),
      ],
    ),
    actions: [
      LzButton.ghost(
        label: 'Cancel',
        onPressed: () => Navigator.of(context).pop(),
      ),
      const SizedBox(width: AppSpacing.sm),
      LzButton.primary(
        label: 'Export',
        icon: Icons.ios_share,
        onPressed: () => Navigator.of(context).pop(ctrl.text),
      ),
    ],
  );
}
