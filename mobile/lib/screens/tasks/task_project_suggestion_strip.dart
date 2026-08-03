/// The Add-Task sheet's live `/token` project suggestion dropdown.
///
/// Extracted verbatim from `add_task_sheet.dart` (which is well past this
/// project's file-size ceiling) into its own file, mirroring how the sibling
/// Add-Expense sheet already keeps its equivalent in
/// `expenses/expense_project_suggestion_strip.dart`. Behaviour is unchanged —
/// this is a move, not a rewrite; the widget went from library-private to
/// public only because it now lives next door instead of inside.
library;

import 'package:flutter/material.dart';

import '../../models/project.dart';
import '../../ui/ui.dart';
import '../expenses/project_color_picker.dart' show ProjectColorDot;

/// Live `/token` project suggestions, shown under the title field while the
/// user is mid-token. Rows are the case-insensitive SUBSTRING matches over
/// [projects] (max 4, exact match first, then prefix, then substring — so an
/// exact match can't be pushed outside the visible window by source-order
/// while `hasExactMatch` still hides the create row), deduped by lowercased
/// name, plus a trailing "Create project '{token}'" row when no project name
/// EXACTLY matches the typed token. Bounded-height inline dropdown, matching
/// the [SheetFormulaHelper]-style autocomplete pattern used elsewhere
/// (sheet_formula_bar.dart).
class TaskProjectSuggestionStrip extends StatelessWidget {
  const TaskProjectSuggestionStrip({
    super.key,
    required this.token,
    required this.projects,
    required this.onSelect,
    required this.onCreate,
  });

  /// The raw token text parsed from the title (no leading `#`/`/`).
  final String token;
  final List<Project> projects;

  /// Called with the exact matched project's name.
  final ValueChanged<String> onSelect;

  /// Called with [token] when the "Create project" row is tapped.
  final ValueChanged<String> onCreate;

  @override
  Widget build(BuildContext context) {
    final needle = token.toLowerCase();
    // Bucket by match strength (exact / prefix / substring) instead of a
    // single source-order filter, then dedupe by lowercased name — a
    // duplicate name would otherwise produce two rows sharing the same
    // ValueKey (a debug assert). Buckets preserve [projects]' original
    // relative order within themselves.
    final exact = <Project>[];
    final prefix = <Project>[];
    final substring = <Project>[];
    final seenNames = <String>{};
    for (final p in projects) {
      final name = p.name.toLowerCase();
      if (!name.contains(needle)) continue;
      if (!seenNames.add(name)) continue;
      if (name == needle) {
        exact.add(p);
      } else if (name.startsWith(needle)) {
        prefix.add(p);
      } else {
        substring.add(p);
      }
    }
    final matches = [...exact, ...prefix, ...substring].take(4).toList();
    final hasExactMatch = exact.isNotEmpty;

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
              key: ValueKey('project-suggest-${p.name}'),
              dense: true,
              leading: ProjectColorDot(hex: p.color, size: 12),
              title: p.name,
              onTap: () => onSelect(p.name),
            ),
          if (!hasExactMatch)
            LzListTile(
              key: const Key('project-suggest-create'),
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
