import 'package:flutter/material.dart';

import '../../ui/ui.dart';

/// A small uppercase section header, matching the add-task sheet's headers.
///
/// Was a private `_SectionLabel` inside `task_detail_sheet.dart`; promoted to
/// its own file so the sibling controls extracted out of that sheet (budget,
/// tags) can render the same header without either importing the sheet or
/// re-declaring a near-identical widget.
class TaskSectionLabel extends StatelessWidget {
  const TaskSectionLabel(this.text, {super.key});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      style: AppText.caption.copyWith(
        color: AppColors.textMuted,
        letterSpacing: 0.8,
        fontWeight: FontWeight.w700,
      ),
    );
  }
}
