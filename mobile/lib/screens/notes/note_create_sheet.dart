import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

/// Bottom-sheet form to create a new note.
///
/// Call [NoteCreateSheet.show] and await the result. Returns
/// `({String title, String content})` when the user taps "Create", or `null`
/// when cancelled.
abstract final class NoteCreateSheet {
  NoteCreateSheet._();

  static Future<({String title, String content})?> show(
    BuildContext context,
  ) {
    return LzBottomSheet.show<({String title, String content})>(
      context,
      title: 'New note',
      builder: (_) => const _CreateForm(),
    );
  }
}

class _CreateForm extends StatefulWidget {
  const _CreateForm();

  @override
  State<_CreateForm> createState() => _CreateFormState();
}

class _CreateFormState extends State<_CreateForm> {
  final _titleCtrl = TextEditingController();
  final _contentCtrl = TextEditingController();

  @override
  void dispose() {
    _titleCtrl.dispose();
    _contentCtrl.dispose();
    super.dispose();
  }

  void _submit() {
    final content = _contentCtrl.text.trim();
    if (content.isEmpty) return;
    Navigator.of(context).pop(
      (title: _titleCtrl.text, content: content),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        LzTextField(
          controller: _titleCtrl,
          label: 'Title',
          hint: 'Untitled (optional)',
          textInputAction: TextInputAction.next,
        ),
        const SizedBox(height: AppSpacing.lg),
        LzTextField(
          controller: _contentCtrl,
          label: 'Content',
          hint: 'Write markdown…',
          minLines: 4,
          maxLines: 8,
          autofocus: true,
          keyboardType: TextInputType.multiline,
          textInputAction: TextInputAction.newline,
        ),
        const SizedBox(height: AppSpacing.xl),
        Row(
          children: [
            Expanded(
              child: LzButton.secondary(
                label: 'Cancel',
                onPressed: () => Navigator.of(context).pop(),
                expand: true,
              ),
            ),
            const SizedBox(width: AppSpacing.md),
            Expanded(
              child: LzButton.primary(
                label: 'Create',
                icon: Icons.add_rounded,
                onPressed: _submit,
                expand: true,
              ),
            ),
          ],
        ),
      ],
    );
  }
}
