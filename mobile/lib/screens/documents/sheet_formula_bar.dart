/// Formula bar + formula-function autocomplete helper for the sheet editor.
///
/// Extracted from sheet_editor_screen.dart when adding row/col ops, TSV
/// copy/paste, sort, and freeze pushed the screen over the 800-line limit.
library;

import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import 'formula_helper.dart';

// ── SheetTabs ─────────────────────────────────────────────────────────────────

/// Horizontally scrollable sheet-tab strip; shown when the workbook has >1 sheet.
class SheetTabs extends StatelessWidget {
  const SheetTabs({
    super.key,
    required this.sheetNames,
    required this.activeIndex,
    required this.onSelect,
  });

  final List<String> sheetNames;
  final int activeIndex;
  final void Function(int) onSelect;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 40,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.sm),
        children: [
          for (var i = 0; i < sheetNames.length; i++)
            Padding(
              padding: const EdgeInsets.only(right: AppSpacing.xs),
              child: ChoiceChip(
                label: Text(sheetNames[i]),
                selected: i == activeIndex,
                onSelected: (_) => onSelect(i),
              ),
            ),
        ],
      ),
    );
  }
}

// ── SheetFormulaBar ───────────────────────────────────────────────────────────

/// Cell-reference label + fx text field + Apply button.
class SheetFormulaBar extends StatelessWidget {
  const SheetFormulaBar({
    super.key,
    required this.cellRef,
    required this.controller,
    required this.focusNode,
    required this.hasSel,
    required this.onChanged,
    required this.onSubmitted,
    required this.onApply,
  });

  final String cellRef;
  final TextEditingController controller;
  final FocusNode focusNode;
  final bool hasSel;
  final VoidCallback onChanged;
  final VoidCallback onSubmitted;
  final VoidCallback onApply;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.xs,
      ),
      decoration: const BoxDecoration(
        color: AppColors.bgSurfaceElevated,
        border: Border(bottom: BorderSide(color: AppColors.borderSubtle)),
      ),
      child: Row(
        children: [
          SizedBox(
            width: 48,
            child: Text(
              cellRef,
              style: AppText.caption.copyWith(
                color: AppColors.textMuted,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
          const Text('ƒx  ', style: TextStyle(color: AppColors.textMuted)),
          Expanded(
            child: TextField(
              controller: controller,
              focusNode: focusNode,
              enabled: hasSel,
              onChanged: (_) => onChanged(),
              onSubmitted: (_) => onSubmitted(),
              textInputAction: TextInputAction.done,
              style: AppText.body.copyWith(fontSize: 14),
              decoration: InputDecoration(
                isDense: true,
                border: InputBorder.none,
                hintText: hasSel ? 'Value or =formula' : 'Tap a cell to edit',
                hintStyle: AppText.body.copyWith(color: AppColors.textMuted),
              ),
            ),
          ),
          if (hasSel)
            LzIconButton(
              icon: Icons.check,
              tooltip: 'Apply',
              onPressed: onApply,
            ),
        ],
      ),
    );
  }
}

// ── SheetFormulaHelper ────────────────────────────────────────────────────────

/// Autocomplete dropdown showing formula function signatures + docs.
class SheetFormulaHelper extends StatelessWidget {
  const SheetFormulaHelper({
    super.key,
    required this.suggestions,
    required this.onTap,
  });

  final List<FormulaFn> suggestions;
  final void Function(FormulaFn fn) onTap;

  @override
  Widget build(BuildContext context) {
    return Container(
      constraints: const BoxConstraints(maxHeight: 168),
      color: AppColors.bgSurface,
      child: ListView.builder(
        shrinkWrap: true,
        itemCount: suggestions.length,
        itemBuilder: (_, i) {
          final f = suggestions[i];
          return ListTile(
            dense: true,
            title: Text(f.signature, style: AppText.body.copyWith(fontSize: 13)),
            subtitle: Text(f.help, style: AppText.caption),
            onTap: () => onTap(f),
          );
        },
      ),
    );
  }
}
