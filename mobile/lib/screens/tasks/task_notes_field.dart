/// The task detail sheet's NOTES block.
///
/// Two states, both extracted verbatim out of `task_detail_sheet.dart`:
///   * a read-only [LinkText] preview when the notes are non-empty and
///     un-touched — tapping anywhere but a link switches to the editor
///     (LinkText's tap recognizers only claim link spans, so a tap on plain
///     text still bubbles up);
///   * the editable field with a trailing "Add link" affordance.
///
/// The sheet keeps owning [controller], [focusNode] and the [editing] flag —
/// this widget only renders them, so the tap-to-edit and add-link behaviors
/// stay exactly as they were.
library;

import 'package:flutter/material.dart';

import '../../ui/ui.dart';
import '../../widgets/link_text.dart';

class TaskNotesField extends StatelessWidget {
  const TaskNotesField({
    super.key,
    required this.controller,
    required this.focusNode,
    required this.editing,
    required this.onBeginEdit,
    required this.onAddLink,
  });

  final TextEditingController controller;
  final FocusNode focusNode;

  /// False renders the read-only preview.
  final bool editing;

  final VoidCallback onBeginEdit;
  final VoidCallback onAddLink;

  @override
  Widget build(BuildContext context) {
    if (!editing) {
      return GestureDetector(
        key: const Key('task-detail-notes-preview'),
        behavior: HitTestBehavior.opaque,
        onTap: onBeginEdit,
        child: Container(
          width: double.infinity,
          padding: const EdgeInsets.all(AppSpacing.md),
          decoration: BoxDecoration(
            color: AppColors.bgSurfaceElevated,
            borderRadius: AppRadii.rMd,
            border: Border.all(color: AppColors.borderDefault),
          ),
          child: LinkText(controller.text, style: AppText.body),
        ),
      );
    }
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: LzTextField(
            controller: controller,
            fieldKey: const Key('task-detail-notes'),
            focusNode: focusNode,
            hint: 'Notes (optional)',
            prefixIcon: Icons.notes_outlined,
            minLines: 2,
            maxLines: 5,
            keyboardType: TextInputType.multiline,
          ),
        ),
        IconButton(
          key: const Key('task-detail-notes-add-link'),
          icon: const Icon(Icons.add_link),
          color: AppColors.textMuted,
          tooltip: 'Add link',
          onPressed: onAddLink,
        ),
      ],
    );
  }
}
