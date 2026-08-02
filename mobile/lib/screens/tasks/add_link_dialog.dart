import 'package:flutter/material.dart';

/// Shows a small "Add link" dialog with "Text" + "URL" fields.
///
/// Returns `'[text](url)'` markdown when the user taps Insert with a valid
/// `http(s)://` URL (an empty text field falls back to the URL itself as the
/// label), or `null` when the dialog is cancelled.
///
/// The dialog's buttons pop the DIALOG's own [dialogCtx], never the caller's
/// [context] — popping the wrong context here would close whatever sheet the
/// dialog was opened from instead of just the dialog (the documented
/// confirm-dialog-over-sheet freeze gotcha).
Future<String?> showAddLinkDialog(BuildContext context) {
  final textCtrl = TextEditingController();
  final urlCtrl = TextEditingController();
  return showDialog<String>(
    context: context,
    builder: (dialogCtx) {
      String? urlError;
      return StatefulBuilder(builder: (dialogCtx, setState) {
        return AlertDialog(
          title: const Text('Add link'),
          content: Column(mainAxisSize: MainAxisSize.min, children: [
            TextField(
                key: const Key('add-link-text'),
                controller: textCtrl,
                decoration: const InputDecoration(labelText: 'Text')),
            TextField(
                key: const Key('add-link-url'),
                controller: urlCtrl,
                keyboardType: TextInputType.url,
                decoration: InputDecoration(
                    labelText: 'URL', errorText: urlError)),
          ]),
          actions: [
            TextButton(
              // Pop the DIALOG's context, never the sheet's — a wrong ctx
              // here freezes the sheet underneath (documented gotcha).
              onPressed: () => Navigator.of(dialogCtx).pop(),
              child: const Text('Cancel'),
            ),
            TextButton(
              key: const Key('add-link-insert'),
              onPressed: () {
                final url = urlCtrl.text.trim();
                if (!RegExp(r'^https?://\S+$').hasMatch(url)) {
                  setState(() => urlError = 'Enter a full http(s):// URL');
                  return;
                }
                final label =
                    textCtrl.text.trim().isEmpty ? url : textCtrl.text.trim();
                Navigator.of(dialogCtx).pop('[$label]($url)');
              },
              child: const Text('Insert'),
            ),
          ],
        );
      });
    },
  );
}
