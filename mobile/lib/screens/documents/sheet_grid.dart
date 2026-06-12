/// The interactive grid widget for the native sheet editor.
///
/// Extracted from `sheet_editor_screen.dart` when adding range-selection and
/// style rendering pushed that file over the 800-line limit.
///
/// Responsibilities:
///   - Render all cells with Univer-style attributes (bold / italic / color /
///     fill / alignment / wrap / number format).
///   - Highlight the selected range with a light accent tint; anchor cell gets
///     a stronger border.
///   - Expose a 12-px drag handle at the bottom-right corner of the selection
///     rect; dragging it extends the selection.
///   - Support long-press to start a range selection.
///   - Column header tap → select whole column; row gutter tap → whole row.
///
/// Gesture coordination notes:
///   The handle detector is placed INSIDE the [InteractiveViewer] child so its
///   coordinates are already in child (grid) space — no transform inversion
///   needed. A pan that STARTS inside the handle zone is intercepted before
///   [InteractiveViewer] sees it; all other pans fall through to
///   [InteractiveViewer]'s built-in pan handler.
library;

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import 'sheet_selection.dart';
import 'univer_model.dart';
import 'univer_ops.dart';
import 'univer_parse.dart';

/// Public grid widget consumed by [SheetEditorScreen].
///
/// Named [SheetEditorGrid] to avoid collision with [SheetGrid] (the read-only
/// parse model in `univer_parse.dart`).
class SheetEditorGrid extends StatefulWidget {
  const SheetEditorGrid({
    super.key,
    required this.sheet,
    required this.rows,
    required this.cols,
    required this.sel,
    required this.viewportWidth,
    required this.onTapCell,
    required this.onExtendSelection,
    required this.onStartSelection,
  });

  final UniverSheet sheet;
  final int rows;
  final int cols;
  final SheetSelection? sel;
  final double viewportWidth;
  final void Function(int row, int col) onTapCell;
  final void Function(int row, int col) onExtendSelection;
  final void Function(int row, int col) onStartSelection;

  @override
  State<SheetEditorGrid> createState() => _SheetEditorGridState();
}

class _SheetEditorGridState extends State<SheetEditorGrid> {
  static const double _gutterW = 44;
  static const double _minColW = 88;
  static const double _maxColW = 160;
  static const double _rowH = 36;
  static const double _handleRadius = 6.0;

  // Whether we are currently in a drag-to-extend gesture.
  bool _draggingHandle = false;

  // Last cell emitted during a handle drag — used to skip redundant callbacks.
  int _dragLastRow = -1;
  int _dragLastCol = -1;

  // Stable gesture recognizer map — created once, never rebuilt per-frame.
  // The initializer lambdas are called once; `this`-bound handler methods
  // stay up-to-date because they are looked up via the live instance.
  late final Map<Type, GestureRecognizerFactory> _gestures = {
    PanGestureRecognizer:
        GestureRecognizerFactoryWithHandlers<PanGestureRecognizer>(
      () => PanGestureRecognizer(),
      (i) => i
        ..onStart = _onPanStart
        ..onUpdate = _onPanUpdate
        ..onEnd = _onPanEnd,
    ),
    LongPressGestureRecognizer:
        GestureRecognizerFactoryWithHandlers<LongPressGestureRecognizer>(
      () => LongPressGestureRecognizer(
          duration: const Duration(milliseconds: 400)),
      (i) => i
        ..onLongPressStart = _onLongPressStart
        ..onLongPressMoveUpdate = _onLongPressMoveUpdate,
    ),
  };

  double get _colW {
    final avail = widget.viewportWidth - _gutterW;
    final fit = avail / widget.cols;
    return fit.clamp(_minColW, _maxColW);
  }

  // ── Handle hit-test ──────────────────────────────────────────────────────────

  /// Returns true if [localPos] is within the drag-handle circle at the
  /// bottom-right corner of the current selection rect.
  bool _onHandle(Offset localPos) {
    final sel = widget.sel;
    if (sel == null) return false;
    final colW = _colW;
    final range = sel.range;

    // Bottom-right corner of the selection in child-space coordinates.
    final handleX = _gutterW + (range.c2 + 1) * colW;
    final handleY = _rowH + (range.r2 + 1) * _rowH; // +1 for header row
    final dist = (localPos - Offset(handleX, handleY)).distance;
    return dist <= _handleRadius + 4; // small tolerance
  }

  void _onPanStart(DragStartDetails details) {
    if (_onHandle(details.localPosition)) {
      setState(() => _draggingHandle = true);
    }
  }

  void _onPanUpdate(DragUpdateDetails details) {
    if (!_draggingHandle) return;
    final (row, col) = cellFromOffset(
      details.localPosition,
      gutterW: _gutterW,
      headerH: _rowH,
      colW: _colW,
      rowH: _rowH,
    );
    if (row >= 0 && col >= 0) {
      // Only fire the callback when the pointer has moved to a different cell.
      // This avoids per-pixel setState rebuilds of the entire editor at ~60 Hz.
      if (row == _dragLastRow && col == _dragLastCol) return;
      _dragLastRow = row;
      _dragLastCol = col;
      widget.onExtendSelection(row, col);
    }
  }

  void _onPanEnd(DragEndDetails _) {
    if (_draggingHandle) {
      _dragLastRow = -1;
      _dragLastCol = -1;
      setState(() => _draggingHandle = false);
    }
  }

  // ── Long-press to start range ────────────────────────────────────────────────

  void _onLongPressStart(LongPressStartDetails details) {
    final (row, col) = cellFromOffset(
      details.localPosition,
      gutterW: _gutterW,
      headerH: _rowH,
      colW: _colW,
      rowH: _rowH,
    );
    if (row >= 0 && col >= 0) {
      widget.onStartSelection(row, col);
    }
  }

  void _onLongPressMoveUpdate(LongPressMoveUpdateDetails details) {
    final (row, col) = cellFromOffset(
      details.localPosition,
      gutterW: _gutterW,
      headerH: _rowH,
      colW: _colW,
      rowH: _rowH,
    );
    if (row >= 0 && col >= 0) {
      widget.onExtendSelection(row, col);
    }
  }

  // ── Header / gutter taps ─────────────────────────────────────────────────────

  void _onTapColHeader(int col) {
    final maxRow = widget.rows - 1;
    // Select full column by extending from row-0 to maxRow with same col.
    widget.onStartSelection(0, col);
    widget.onExtendSelection(maxRow, col);
  }

  void _onTapRowGutter(int row) {
    final maxCol = widget.cols - 1;
    widget.onStartSelection(row, 0);
    widget.onExtendSelection(row, maxCol);
  }

  // ── Build ────────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final colW = _colW;
    final sel = widget.sel;

    // The gesture layer sits inside InteractiveViewer so its local coordinate
    // system already matches grid-space — no transform inversion required.
    return InteractiveViewer(
      panEnabled: !_draggingHandle, // let our drag handler win
      scaleEnabled: true,
      minScale: 0.5,
      maxScale: 3.0,
      constrained: false,
      child: RawGestureDetector(
        gestures: _gestures,
        child: SingleChildScrollView(
          scrollDirection: Axis.vertical,
          child: SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Stack(
              children: [
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    _headerRow(colW),
                    for (var r = 0; r < widget.rows; r++) _dataRow(r, colW),
                  ],
                ),
                // Drag handle overlay.
                if (sel != null) _handleOverlay(sel, colW),
              ],
            ),
          ),
        ),
      ),
    );
  }

  // ── Handle overlay ───────────────────────────────────────────────────────────

  Widget _handleOverlay(SheetSelection sel, double colW) {
    final range = sel.range;
    final handleX = _gutterW + (range.c2 + 1) * colW - _handleRadius;
    final handleY = _rowH + (range.r2 + 1) * _rowH - _handleRadius; // +1 header
    return Positioned(
      left: handleX,
      top: handleY,
      child: Container(
        width: _handleRadius * 2,
        height: _handleRadius * 2,
        decoration: BoxDecoration(
          color: AppColors.accent,
          shape: BoxShape.circle,
          border: Border.all(color: AppColors.bgSurface, width: 1.5),
        ),
      ),
    );
  }

  // ── Header row ───────────────────────────────────────────────────────────────

  Widget _headerRow(double colW) {
    return Row(
      children: [
        _gutter(''),
        for (var c = 0; c < widget.cols; c++) _colHeader(c, colW),
      ],
    );
  }

  Widget _colHeader(int c, double colW) {
    return GestureDetector(
      onTap: () => _onTapColHeader(c),
      child: Container(
        width: colW,
        height: _rowH,
        alignment: Alignment.center,
        decoration: const BoxDecoration(
          color: AppColors.bgSurfaceElevated,
          border: Border(
            right: BorderSide(color: AppColors.borderSubtle),
            bottom: BorderSide(color: AppColors.borderSubtle),
          ),
        ),
        child: Text(
          colToLetter(c),
          style: AppText.caption.copyWith(
            color: AppColors.textSecondary,
            fontWeight: FontWeight.w700,
          ),
        ),
      ),
    );
  }

  // ── Data row ─────────────────────────────────────────────────────────────────

  Widget _dataRow(int r, double colW) {
    return Row(
      children: [
        _rowGutter(r),
        for (var c = 0; c < widget.cols; c++) _cell(r, c, colW),
      ],
    );
  }

  Widget _rowGutter(int r) {
    return GestureDetector(
      onTap: () => _onTapRowGutter(r),
      child: _gutter('${r + 1}'),
    );
  }

  Widget _gutter(String text) => Container(
        width: _gutterW,
        height: _rowH,
        alignment: Alignment.center,
        decoration: const BoxDecoration(
          color: AppColors.bgSurfaceElevated,
          border: Border(
            right: BorderSide(color: AppColors.borderSubtle),
            bottom: BorderSide(color: AppColors.borderSubtle),
          ),
        ),
        child:
            Text(text, style: AppText.caption.copyWith(color: AppColors.textMuted)),
      );

  // ── Cell ─────────────────────────────────────────────────────────────────────

  Widget _cell(int r, int c, double colW) {
    final sel = widget.sel;
    final inRange = sel != null && sel.range.contains(r, c);
    final isAnchor =
        sel != null && r == sel.anchorRow && c == sel.anchorCol;

    final univCell = widget.sheet.cellAt(r, c);
    final style = widget.sheet.resolveStyle(r, c);

    // Compute display text, applying number format when present.
    final displayText = style.numFmt != null
        ? formatNumber(univCell.value, style.numFmt)
        : univCell.display;

    // Background: fill color > selection tint > default surface.
    Color bgColor;
    if (style.bgColor != null) {
      bgColor = parseHex(style.bgColor!) ?? AppColors.bgSurface;
    } else if (inRange) {
      bgColor = AppColors.accent.withValues(alpha: 0.16);
    } else {
      bgColor = AppColors.bgSurface;
    }

    // Border: anchor cell gets stronger accent border; in-range gets lighter
    // accent; otherwise hairline.
    final border = isAnchor
        ? Border.all(color: AppColors.accent, width: 1.5)
        : inRange
            ? Border.all(
                color: AppColors.accent.withValues(alpha: 0.5),
                width: 0.75,
              )
            : Border.all(color: AppColors.borderSubtle, width: 0.5);

    // Check for hyperlink on this cell — linked cells override text color.
    final hasLink = widget.sheet.linkAt(r, c) != null;

    // Text color: link overrides style color.
    final textColor = hasLink
        ? AppColors.accent
        : style.color != null
            ? (parseHex(style.color!) ?? AppColors.textPrimary)
            : AppColors.textPrimary;

    // Alignment.
    Alignment alignment;
    switch (style.hAlign) {
      case 2:
        alignment = Alignment.center;
      case 3:
        alignment = Alignment.centerRight;
      default:
        if (style.hAlign == 1) {
          alignment = Alignment.centerLeft;
        } else {
          // hAlign == 0 auto: numbers right, text left.
          final isNum = univCell.value is num ||
              (univCell.value != null &&
                  num.tryParse(univCell.value.toString().trim()) != null);
          alignment = isNum ? Alignment.centerRight : Alignment.centerLeft;
        }
    }

    final textStyle = AppText.body.copyWith(
      fontSize: 13,
      color: textColor,
      fontWeight: style.bold ? FontWeight.w700 : FontWeight.w400,
      fontStyle: style.italic ? FontStyle.italic : FontStyle.normal,
      decoration: TextDecoration.combine([
        if (style.underline || hasLink) TextDecoration.underline,
        if (style.strike) TextDecoration.lineThrough,
      ]),
      decorationColor: textColor,
    );

    return GestureDetector(
      onTap: () => widget.onTapCell(r, c),
      child: Container(
        width: colW,
        height: _rowH,
        alignment: alignment,
        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.sm),
        decoration: BoxDecoration(
          color: bgColor,
          border: border,
        ),
        child: style.wrap
            ? Text(
                displayText,
                style: textStyle,
                softWrap: true,
                overflow: TextOverflow.clip,
              )
            : Text(
                displayText,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: textStyle,
              ),
      ),
    );
  }

}
