/// Conflict-resolution banner and extracted build helpers for the sheet editor.
///
/// Exports:
///   • [SheetConflictBanner] – amber banner rendered on 409 conflicts.
///   • [SheetAppBarActions]  – appBar action row for SheetEditorScreen.
///   • [SheetEditorBody]     – column + grid body for SheetEditorScreen.
///
/// Extracted to keep sheet_editor_screen.dart under the 800-line limit.
library;

import 'package:flutter/material.dart';

import '../../repositories/documents_repository.dart';
import '../../ui/ui.dart';
import 'formula_helper.dart';
import 'sheet_formula_bar.dart';
import 'sheet_grid.dart';
import 'sheet_link_ui.dart';
import 'sheet_selection.dart';
import 'sheet_toolbar.dart';
import 'univer_model.dart';
import 'univer_ops.dart';
import 'univer_parse.dart';

/// A full-width amber banner that sits above the editor toolbar when a
/// save operation raises a [DocConflictException].
///
/// Parameters [onReload] and [onKeepMine] must not be null — the banner is
/// only rendered when a conflict is active.
class SheetConflictBanner extends StatelessWidget {
  const SheetConflictBanner({
    super.key,
    required this.onReload,
    required this.onKeepMine,
    this.label = 'Sheet changed on the server.',
  });

  /// "Reload" action: adopt the server version.
  final VoidCallback onReload;

  /// "Keep mine" action: force-save local version (LWW).
  final VoidCallback onKeepMine;

  /// Message text — override for the doc editor.
  final String label;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: AppColors.warn.withValues(alpha: 0.14),
      child: Padding(
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.sm,
        ),
        child: Row(
          children: [
            const Icon(Icons.sync_problem_outlined,
                color: AppColors.warn, size: 18),
            const SizedBox(width: AppSpacing.md),
            Expanded(
              child: Text(
                label,
                style: AppText.caption.copyWith(
                  color: AppColors.warn,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
            _Action(
              label: 'Reload',
              onTap: onReload,
            ),
            const SizedBox(width: AppSpacing.xs),
            _Action(
              label: 'Keep mine',
              onTap: onKeepMine,
            ),
          ],
        ),
      ),
    );
  }
}

// ── Internal action button ────────────────────────────────────────────────────

class _Action extends StatelessWidget {
  const _Action({required this.label, required this.onTap});

  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: AppRadii.rSm,
      child: ConstrainedBox(
        constraints: const BoxConstraints(minHeight: 44),
        child: Padding(
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.sm,
            vertical: AppSpacing.xs,
          ),
          child: Center(
            widthFactor: 1,
            child: Text(
              label,
              style: AppText.caption.copyWith(
                color: AppColors.warn,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

// ── SheetAppBarActions ────────────────────────────────────────────────────────

/// AppBar action row for [SheetEditorScreen]: saving indicator, export/share
/// popup menu, and the AI edit button.
class SheetAppBarActions extends StatelessWidget {
  const SheetAppBarActions({
    super.key,
    required this.saving,
    required this.applying,
    required this.onExport,
    required this.onConvertLinks,
    required this.onAi,
  });

  final bool saving;
  final bool applying;

  /// Called with (format, ext, mime) when the user picks an export option.
  final void Function(String format, String ext, String mime) onExport;
  final VoidCallback onConvertLinks;
  final VoidCallback onAi;

  static const _xlsxMime =
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (saving)
          const Padding(
            padding: EdgeInsets.symmetric(horizontal: AppSpacing.md),
            child: Center(
              child: SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: AppColors.accent,
                ),
              ),
            ),
          ),
        PopupMenuButton<String>(
          icon: const Icon(Icons.ios_share, color: AppColors.textSecondary),
          tooltip: 'Export / share',
          color: AppColors.bgSurfaceElevated,
          onSelected: (v) {
            if (v == 'xlsx') {
              onExport('xlsx', 'xlsx', _xlsxMime);
            } else if (v == 'csv') {
              onExport('csv', 'csv', 'text/csv');
            } else if (v == 'convert_links') {
              onConvertLinks();
            }
          },
          itemBuilder: (_) => const [
            PopupMenuItem(value: 'xlsx', child: Text('Export as Excel (.xlsx)')),
            PopupMenuItem(value: 'csv', child: Text('Export as CSV (.csv)')),
            PopupMenuDivider(),
            PopupMenuItem(
              value: 'convert_links',
              child: Text('Convert URLs to links'),
            ),
          ],
        ),
        LzIconButton(
          icon: Icons.auto_awesome,
          tooltip: 'Ask AI to edit',
          accent: true,
          onPressed: applying ? null : onAi,
        ),
      ],
    );
  }
}

// ── SheetEditorBody ───────────────────────────────────────────────────────────

/// The full column+grid body for [SheetEditorScreen]: conflict banner, sheet
/// tabs, toolbar, link chip, formula bar, formula helper, and the data grid.
///
/// Extracted to keep [SheetEditorScreen] under 800 lines.
class SheetEditorBody extends StatelessWidget {
  const SheetEditorBody({
    super.key,
    required this.loading,
    required this.error,
    required this.onRetry,
    required this.sheet,
    required this.sel,
    required this.anchorStyle,
    required this.canUndo,
    required this.canRedo,
    required this.conflict,
    required this.suggestions,
    required this.formulaCtrl,
    required this.formulaFocus,
    required this.gridRows,
    required this.gridCols,
    required this.onConflictReload,
    required this.onConflictKeepMine,
    required this.onTabSelect,
    required this.onToolbarAction,
    required this.onTextColor,
    required this.onFillColor,
    required this.onNumberFormat,
    required this.onEditLink,
    required this.onRemoveLink,
    required this.onSnack,
    required this.onFormulaChanged,
    required this.onFormulaSubmit,
    required this.onInsertFunction,
    required this.onTapCell,
    required this.onExtendSelection,
    required this.onStartSelection,
    required this.onHeaderAction,
    this.onResizeCol,
    this.onResizeRow,
  });

  final bool loading;
  final String? error;
  final VoidCallback onRetry;
  final UniverSheet? sheet;
  final SheetSelection? sel;
  final CellStyleView anchorStyle;
  final bool canUndo;
  final bool canRedo;
  final DocPayload? conflict;
  final List<FormulaFn> suggestions;
  final TextEditingController formulaCtrl;
  final FocusNode formulaFocus;
  final int gridRows;
  final int gridCols;
  final VoidCallback onConflictReload;
  final VoidCallback onConflictKeepMine;
  final void Function(int) onTabSelect;
  final void Function(SheetToolbarAction) onToolbarAction;
  final void Function(String?) onTextColor;
  final void Function(String?) onFillColor;
  final void Function(String?) onNumberFormat;
  final VoidCallback onEditLink;
  final VoidCallback onRemoveLink;
  final void Function(String msg, {bool error}) onSnack;
  final VoidCallback onFormulaChanged;
  final VoidCallback onFormulaSubmit;
  final void Function(FormulaFn) onInsertFunction;
  final void Function(int row, int col) onTapCell;
  final void Function(int row, int col) onExtendSelection;
  final void Function(int row, int col) onStartSelection;
  final void Function(SheetHeaderAction action, int index) onHeaderAction;
  final void Function(int col, double width)? onResizeCol;
  final void Function(int row, double height)? onResizeRow;

  @override
  Widget build(BuildContext context) {
    if (loading) return LzSkeleton.list(count: 5);
    if (error != null) return LzErrorState(message: error!, onRetry: onRetry);
    final s = sheet;
    // No sheet, not loading, no error — previously `SizedBox.shrink()`, i.e. a
    // featureless near-black screen with no message and no way out. Always give
    // the user something to act on.
    if (s == null) {
      return LzErrorState(
        message: 'Could not open this sheet. Pull to retry.',
        onRetry: onRetry,
      );
    }
    return Column(
      children: [
        if (conflict != null)
          SheetConflictBanner(
            onReload: onConflictReload,
            onKeepMine: onConflictKeepMine,
          ),
        if (s.sheetNames.length > 1)
          SheetTabs(
            sheetNames: s.sheetNames,
            activeIndex: s.activeIndex,
            onSelect: onTabSelect,
          ),
        if (sel != null)
          SheetToolbar(
            anchorStyle: anchorStyle,
            canUndo: canUndo,
            canRedo: canRedo,
            frozen: s.frozen,
            hasSelection: true,
            onAction: onToolbarAction,
            onTextColor: onTextColor,
            onFillColor: onFillColor,
            onNumberFormat: onNumberFormat,
          ),
        if (sel != null && sel!.isSingle)
          LinkChip(
            sheet: s,
            sel: sel!,
            onEdit: onEditLink,
            onRemove: onRemoveLink,
            onSnack: onSnack,
          ),
        SheetFormulaBar(
          cellRef: sel != null
              ? '${colToLetter(sel!.anchorCol)}${sel!.anchorRow + 1}'
              : '—',
          controller: formulaCtrl,
          focusNode: formulaFocus,
          hasSel: sel != null,
          onChanged: onFormulaChanged,
          onSubmitted: onFormulaSubmit,
          onApply: onFormulaSubmit,
        ),
        if (suggestions.isNotEmpty)
          SheetFormulaHelper(
            suggestions: suggestions,
            onTap: onInsertFunction,
          ),
        Expanded(
          child: LayoutBuilder(
            builder: (context, constraints) => SheetEditorGrid(
              sheet: s,
              rows: gridRows,
              cols: gridCols,
              sel: sel,
              viewportWidth: constraints.maxWidth,
              onTapCell: onTapCell,
              onExtendSelection: onExtendSelection,
              onStartSelection: onStartSelection,
              onHeaderAction: onHeaderAction,
              onResizeCol: onResizeCol,
              onResizeRow: onResizeRow,
            ),
          ),
        ),
      ],
    );
  }
}
