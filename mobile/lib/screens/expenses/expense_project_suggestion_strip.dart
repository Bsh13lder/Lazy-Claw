import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/models/project.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import 'project_color_picker.dart';

/// Live `#`/`/` project suggestions for the Add Expense sheet's Description
/// field, shown only while the parsed token has no unambiguous existing-
/// project match (see `add_expense_sheet.dart:_AddExpenseSheetState.build` —
/// an unambiguous match, per `core/project_resolver.dart`'s exact/substring/
/// fuzzy tiers, is applied silently and never reaches this strip). Rows are
/// case-insensitive prefix/substring matches over [projects] (max 4), deduped
/// by lowercased name, plus a trailing "Create project '{token}'" row —
/// mirrors `add_task_sheet.dart:_ProjectSuggestionStrip`'s pattern (trimmed:
/// no "exact match" bucket here since an exact match is never shown alongside
/// this strip in the first place).
class ExpenseProjectSuggestionStrip extends StatelessWidget {
  const ExpenseProjectSuggestionStrip({
    super.key,
    required this.token,
    required this.projects,
    required this.onSelect,
    required this.onCreate,
  });

  /// The raw token text parsed from the description (no leading `#`/`/`).
  final String token;
  final List<Project> projects;

  /// Called with the matched project's name.
  final ValueChanged<String> onSelect;

  /// Called with [token] when the "Create project" row is tapped.
  final ValueChanged<String> onCreate;

  @override
  Widget build(BuildContext context) {
    final needle = token.toLowerCase();
    final prefix = <Project>[];
    final substring = <Project>[];
    final seenNames = <String>{};
    for (final p in projects) {
      final name = p.name.toLowerCase();
      if (!name.contains(needle)) continue;
      if (!seenNames.add(name)) continue;
      if (name.startsWith(needle)) {
        prefix.add(p);
      } else {
        substring.add(p);
      }
    }
    final matches = [...prefix, ...substring].take(4).toList();

    return Container(
      margin: const EdgeInsets.only(top: AppSpacing.xs),
      constraints: const BoxConstraints(maxHeight: 168),
      decoration: BoxDecoration(
        color: AppColors.bgSurfaceElevated,
        borderRadius: AppRadii.rMd,
        border: Border.all(color: AppColors.borderDefault),
      ),
      child: ListView(
        shrinkWrap: true,
        padding: EdgeInsets.zero,
        children: [
          for (final p in matches)
            LzListTile(
              key: ValueKey('expense-project-suggest-${p.name}'),
              dense: true,
              leading: ProjectColorDot(hex: p.color, size: 12),
              title: p.name,
              onTap: () => onSelect(p.name),
            ),
          LzListTile(
            key: const Key('expense-project-suggest-create'),
            dense: true,
            leading: Icon(
              Icons.add_rounded,
              size: 16,
              color: AppColors.accent,
            ),
            title: "Create project '$token'",
            titleStyle: AppText.body.copyWith(
              color: AppColors.accent,
              fontWeight: FontWeight.w600,
            ),
            onTap: () => onCreate(token),
          ),
        ],
      ),
    );
  }
}
