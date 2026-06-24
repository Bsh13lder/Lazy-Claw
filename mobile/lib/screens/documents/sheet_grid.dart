/// The interactive grid widget for the native sheet editor.
///
/// Extracted from `sheet_editor_screen.dart` when adding range-selection and
/// style rendering pushed that file over the 800-line limit.
///
/// Responsibilities:
///   - Render all cells with Univer-style attributes (bold / italic / color /
///     fill / alignment / wrap / number format) at PER-COLUMN widths and
///     PER-ROW heights read from the stored `columnData[col].w` / `rowData[row].h`
///     (falling back to a computed default). Content is CONTAINED — a cell never
///     bleeds its text into its neighbours (clip + ellipsis, or wrap when the
///     cell style requests it).
///   - Highlight the selected range with a light accent tint; anchor cell gets
///     a stronger border.
///   - Expose a 12-px drag handle at the bottom-right corner of the selection
///     rect; dragging it extends the selection.
///   - Drag-to-resize column borders (and row borders) live, persisting the new
///     size via [onResizeCol] / [onResizeRow].
///   - Support long-press to start a range selection.
///   - Column header tap → select whole column; row gutter tap → whole row.
///   - Long-press a column header / row gutter → context menu (incl. auto-fit).
///
/// Gesture coordination notes:
///   The handle detector is placed INSIDE the [InteractiveViewer] child so its
///   coordinates are already in child (grid) space — no transform inversion
///   needed. A pan that STARTS inside the selection handle OR a column/row
///   resize border is intercepted before [InteractiveViewer] sees it; all other
///   pans fall through to [InteractiveViewer]'s built-in pan handler.
library;

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:lazyclaw_mobile/ui/ui.dart';

import 'sheet_link_ui.dart'
    show kDefaultColW, resolveCurrentColWidth, resolveCurrentRowHeight;
import 'sheet_selection.dart';
import 'univer_links.dart';
import 'univer_model.dart';
import 'univer_ops.dart';
import 'univer_parse.dart';

// ── SheetHeaderAction ─────────────────────────────────────────────────────────

/// Actions emitted by long-pressing a column header or row gutter.
enum SheetHeaderAction {
  // Column actions (long-press on column header letter)
  insertLeft,
  insertRight,
  deleteCol,
  clearCol,
  sortAsc,
  sortDesc,
  colWidth,
  autoFitCol,
  // Row actions (long-press on row gutter number)
  insertAbove,
  insertBelow,
  deleteRow,
  clearRow,
}

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
    this.onHeaderAction,
    this.onResizeCol,
    this.onResizeRow,
  });

  final UniverSheet sheet;
  final int rows;
  final int cols;
  final SheetSelection? sel;
  final double viewportWidth;
  final void Function(int row, int col) onTapCell;
  final void Function(int row, int col) onExtendSelection;
  final void Function(int row, int col) onStartSelection;

  /// Called when the user long-presses a column header or row gutter and selects
  /// an action from the context menu. [index] is the 0-based column or row index.
  final void Function(SheetHeaderAction action, int index)? onHeaderAction;

  /// Called when a column border is dragged to resize [col] to [width] px.
  final void Function(int col, double width)? onResizeCol;

  /// Called when a row border is dragged to resize [row] to [height] px.
  final void Function(int row, double height)? onResizeRow;

  @override
  State<SheetEditorGrid> createState() => _SheetEditorGridState();
}

class _SheetEditorGridState extends State<SheetEditorGrid> {
  static const double _gutterW = 44;
  static const double _minColW = 56;
  static const double _maxColW = 320;
  static const double _minRowH = 24;
  static const double _maxRowH = 240;
  static const double _defaultRowH = 36;
  static const double _handleRadius = 6.0;

  /// Half-width of a resize-border hit zone (px on either side of the border).
  static const double _resizeHitTol = 7.0;

  // Whether we are currently in a drag-to-extend-selection gesture.
  bool _draggingHandle = false;

  // Active column/row resize drag (null when not resizing).
  int? _resizingCol;
  int? _resizingRow;
  double _resizeStartPx = 0;
  double _resizeStartSize = 0;
  // Live preview size during a resize drag (applied to the grid; persisted on end).
  double? _resizePreview;

  // Last cell emitted during a selection-handle drag — skip redundant callbacks.
  int _dragLastRow = -1;
  int _dragLastCol = -1;

  // Stable gesture recognizer map — created once, never rebuilt per-frame.
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

  // ── Per-column widths / per-row heights ──────────────────────────────────────

  /// A reasonable default width when no column has an explicit stored width:
  /// fit the viewport, clamped so columns stay tappable and readable.
  double get _defaultColW {
    final avail = widget.viewportWidth - _gutterW;
    final fit = widget.cols > 0 ? avail / widget.cols : avail;
    return fit.clamp(88.0, 160.0);
  }

  /// Width of column [c]: the stored `columnData[c].w` when present, else the
  /// computed default. A live resize preview overrides for the dragged column.
  double _colWidth(int c) {
    if (_resizingCol == c && _resizePreview != null) {
      return _resizePreview!.clamp(_minColW, _maxColW);
    }
    final stored = resolveCurrentColWidth(widget.sheet, c);
    // resolveCurrentColWidth returns the Univer default (88) when unset — treat
    // that sentinel as "use the viewport-fit default" so narrow sheets still
    // fill the width, but honour any explicit stored width verbatim.
    if (stored == kDefaultColW && !_hasStoredColWidth(c)) {
      return _defaultColW;
    }
    return stored.clamp(_minColW, _maxColW);
  }

  /// Whether column [c] has an EXPLICIT stored width (vs the resolver default).
  bool _hasStoredColWidth(int c) {
    final wb = widget.sheet.rawWorkbook;
    final sheets = wb['sheets'];
    if (sheets is! Map) return false;
    final order = (wb['sheetOrder'] as List?)?.map((e) => e.toString()).toList() ??
        sheets.keys.map((e) => e.toString()).toList();
    final idx =
        widget.sheet.activeIndex.clamp(0, order.isEmpty ? 0 : order.length - 1);
    final sd = sheets[order.isEmpty ? '' : order[idx]];
    if (sd is! Map) return false;
    final cd = sd['columnData'];
    if (cd is! Map) return false;
    final entry = cd[c.toString()];
    return entry is Map && entry['w'] is num;
  }

  /// Height of row [r]: stored `rowData[r].h` when present, else the default.
  /// A live resize preview overrides for the dragged row.
  double _rowHeight(int r) {
    if (_resizingRow == r && _resizePreview != null) {
      return _resizePreview!.clamp(_minRowH, _maxRowH);
    }
    final h = resolveCurrentRowHeight(widget.sheet, r);
    return h.clamp(_minRowH, _maxRowH);
  }

  double get _headerH => _defaultRowH;

  /// Left x-edge of column [c] (after the gutter) = gutter + sum of prior widths.
  double _colLeft(int c) {
    var x = _gutterW;
    for (var i = 0; i < c; i++) {
      x += _colWidth(i);
    }
    return x;
  }

  /// Top y-edge of row [r] (after the header) = header + sum of prior heights.
  double _rowTop(int r) {
    var y = _headerH;
    for (var i = 0; i < r; i++) {
      y += _rowHeight(i);
    }
    return y;
  }

  /// Map a child-space pixel offset to a (row, col) honouring variable sizes.
  (int row, int col) _cellFrom(Offset local) {
    final x = local.dx;
    final y = local.dy;
    int col = -1;
    if (x >= _gutterW) {
      var acc = _gutterW;
      col = widget.cols - 1; // default to last when past the right edge
      for (var c = 0; c < widget.cols; c++) {
        final w = _colWidth(c);
        if (x < acc + w) {
          col = c;
          break;
        }
        acc += w;
      }
    }
    int row = -1;
    if (y >= _headerH) {
      var acc = _headerH;
      row = widget.rows - 1;
      for (var r = 0; r < widget.rows; r++) {
        final h = _rowHeight(r);
        if (y < acc + h) {
          row = r;
          break;
        }
        acc += h;
      }
    }
    if (y < _headerH) {
      return (-1, x < _gutterW ? -1 : (col < 0 ? 0 : col));
    }
    if (x < _gutterW) return (row < 0 ? 0 : row, -1);
    return (row < 0 ? 0 : row, col < 0 ? 0 : col);
  }

  // ── Resize-border hit-tests ──────────────────────────────────────────────────

  /// Returns the column index whose RIGHT border is within [_resizeHitTol] of
  /// [local], when the pointer is in the header band (so a normal cell tap isn't
  /// stolen). Null when not near a column border.
  int? _colResizeHit(Offset local) {
    if (widget.onResizeCol == null) return null;
    if (local.dy > _headerH) return null; // only the header band resizes columns
    if (local.dx < _gutterW) return null;
    var right = _gutterW;
    for (var c = 0; c < widget.cols; c++) {
      right += _colWidth(c);
      if ((local.dx - right).abs() <= _resizeHitTol) return c;
    }
    return null;
  }

  /// Returns the row index whose BOTTOM border is within [_resizeHitTol] of
  /// [local], when the pointer is in the gutter band. Null when not near a row
  /// border.
  int? _rowResizeHit(Offset local) {
    if (widget.onResizeRow == null) return null;
    if (local.dx > _gutterW) return null; // only the gutter band resizes rows
    if (local.dy < _headerH) return null;
    var bottom = _headerH;
    for (var r = 0; r < widget.rows; r++) {
      bottom += _rowHeight(r);
      if ((local.dy - bottom).abs() <= _resizeHitTol) return r;
    }
    return null;
  }

  // ── Selection-handle hit-test ────────────────────────────────────────────────

  /// True if [localPos] is within the drag-handle circle at the bottom-right
  /// corner of the current selection rect.
  bool _onHandle(Offset localPos) {
    final sel = widget.sel;
    if (sel == null) return false;
    final range = sel.range;
    final handleX = _colLeft(range.c2) + _colWidth(range.c2);
    final handleY = _rowTop(range.r2) + _rowHeight(range.r2);
    final dist = (localPos - Offset(handleX, handleY)).distance;
    return dist <= _handleRadius + 4;
  }

  // ── Pan ──────────────────────────────────────────────────────────────────────

  void _onPanStart(DragStartDetails details) {
    final local = details.localPosition;
    // Priority: selection handle > column resize > row resize.
    if (_onHandle(local)) {
      setState(() => _draggingHandle = true);
      return;
    }
    final colHit = _colResizeHit(local);
    if (colHit != null) {
      setState(() {
        _resizingCol = colHit;
        _resizeStartPx = local.dx;
        _resizeStartSize = _colWidth(colHit);
        _resizePreview = _resizeStartSize;
      });
      return;
    }
    final rowHit = _rowResizeHit(local);
    if (rowHit != null) {
      setState(() {
        _resizingRow = rowHit;
        _resizeStartPx = local.dy;
        _resizeStartSize = _rowHeight(rowHit);
        _resizePreview = _resizeStartSize;
      });
    }
  }

  void _onPanUpdate(DragUpdateDetails details) {
    if (_resizingCol != null) {
      final delta = details.localPosition.dx - _resizeStartPx;
      setState(() => _resizePreview =
          (_resizeStartSize + delta).clamp(_minColW, _maxColW));
      return;
    }
    if (_resizingRow != null) {
      final delta = details.localPosition.dy - _resizeStartPx;
      setState(() => _resizePreview =
          (_resizeStartSize + delta).clamp(_minRowH, _maxRowH));
      return;
    }
    if (!_draggingHandle) return;
    final (row, col) = _cellFrom(details.localPosition);
    if (row >= 0 && col >= 0) {
      if (row == _dragLastRow && col == _dragLastCol) return;
      _dragLastRow = row;
      _dragLastCol = col;
      widget.onExtendSelection(row, col);
    }
  }

  void _onPanEnd(DragEndDetails _) {
    if (_resizingCol != null) {
      final col = _resizingCol!;
      final size = _resizePreview ?? _resizeStartSize;
      widget.onResizeCol?.call(col, size);
      setState(() {
        _resizingCol = null;
        _resizePreview = null;
      });
      return;
    }
    if (_resizingRow != null) {
      final row = _resizingRow!;
      final size = _resizePreview ?? _resizeStartSize;
      widget.onResizeRow?.call(row, size);
      setState(() {
        _resizingRow = null;
        _resizePreview = null;
      });
      return;
    }
    if (_draggingHandle) {
      _dragLastRow = -1;
      _dragLastCol = -1;
      setState(() => _draggingHandle = false);
    }
  }

  // ── Long-press to start range ────────────────────────────────────────────────

  void _onLongPressStart(LongPressStartDetails details) {
    final (row, col) = _cellFrom(details.localPosition);
    if (row >= 0 && col >= 0) {
      widget.onStartSelection(row, col);
    }
  }

  void _onLongPressMoveUpdate(LongPressMoveUpdateDetails details) {
    final (row, col) = _cellFrom(details.localPosition);
    if (row >= 0 && col >= 0) {
      widget.onExtendSelection(row, col);
    }
  }

  // ── Header / gutter taps ─────────────────────────────────────────────────────

  void _onTapColHeader(int col) {
    final maxRow = widget.rows - 1;
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
    final sel = widget.sel;
    return InteractiveViewer(
      panEnabled: !_draggingHandle && _resizingCol == null && _resizingRow == null,
      scaleEnabled: _resizingCol == null && _resizingRow == null,
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
                    _headerRow(),
                    for (var r = 0; r < widget.rows; r++) _dataRow(r),
                  ],
                ),
                if (sel != null) _handleOverlay(sel),
                if (_resizingCol != null) _colResizeGuide(_resizingCol!),
                if (_resizingRow != null) _rowResizeGuide(_resizingRow!),
              ],
            ),
          ),
        ),
      ),
    );
  }

  // ── Resize guides (thin accent line shown while dragging a border) ────────────

  Widget _colResizeGuide(int col) {
    final x = _colLeft(col) + _colWidth(col);
    final totalH = _headerH +
        [for (var r = 0; r < widget.rows; r++) _rowHeight(r)]
            .fold<double>(0, (a, b) => a + b);
    return Positioned(
      left: x - 1,
      top: 0,
      child: Container(width: 2, height: totalH, color: AppColors.accent),
    );
  }

  Widget _rowResizeGuide(int row) {
    final y = _rowTop(row) + _rowHeight(row);
    final totalW = _gutterW +
        [for (var c = 0; c < widget.cols; c++) _colWidth(c)]
            .fold<double>(0, (a, b) => a + b);
    return Positioned(
      left: 0,
      top: y - 1,
      child: Container(width: totalW, height: 2, color: AppColors.accent),
    );
  }

  // ── Handle overlay ───────────────────────────────────────────────────────────

  Widget _handleOverlay(SheetSelection sel) {
    final range = sel.range;
    final handleX = _colLeft(range.c2) + _colWidth(range.c2) - _handleRadius;
    final handleY = _rowTop(range.r2) + _rowHeight(range.r2) - _handleRadius;
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

  Widget _headerRow() {
    return Row(
      children: [
        _gutter('', _gutterW),
        for (var c = 0; c < widget.cols; c++) _colHeader(c),
      ],
    );
  }

  Widget _colHeader(int c) {
    return GestureDetector(
      onTap: () => _onTapColHeader(c),
      onLongPressStart: (d) => _showColMenu(context, d.globalPosition, c),
      child: Container(
        width: _colWidth(c),
        height: _headerH,
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

  void _showColMenu(BuildContext ctx, Offset globalPos, int col) {
    final onAction = widget.onHeaderAction;
    if (onAction == null) return;
    showMenu<SheetHeaderAction>(
      context: ctx,
      color: AppColors.bgSurfaceElevated,
      position: RelativeRect.fromLTRB(
        globalPos.dx,
        globalPos.dy,
        globalPos.dx + 1,
        globalPos.dy + 1,
      ),
      items: [
        _menuItem(SheetHeaderAction.insertLeft, 'Insert left'),
        _menuItem(SheetHeaderAction.insertRight, 'Insert right'),
        _menuItem(SheetHeaderAction.deleteCol, 'Delete column'),
        _menuItem(SheetHeaderAction.clearCol, 'Clear column'),
        const PopupMenuDivider(),
        _menuItem(SheetHeaderAction.sortAsc, 'Sort A → Z'),
        _menuItem(SheetHeaderAction.sortDesc, 'Sort Z → A'),
        const PopupMenuDivider(),
        _menuItem(SheetHeaderAction.autoFitCol, 'Auto-fit width'),
        _menuItem(SheetHeaderAction.colWidth, 'Column width…'),
      ],
    ).then((action) {
      if (action != null) onAction(action, col);
    });
  }

  // ── Data row ─────────────────────────────────────────────────────────────────

  Widget _dataRow(int r) {
    return Row(
      children: [
        _rowGutter(r),
        for (var c = 0; c < widget.cols; c++) _cell(r, c),
      ],
    );
  }

  Widget _rowGutter(int r) {
    return GestureDetector(
      onTap: () => _onTapRowGutter(r),
      onLongPressStart: (d) => _showRowMenu(context, d.globalPosition, r),
      child: _gutter('${r + 1}', _gutterW, height: _rowHeight(r)),
    );
  }

  void _showRowMenu(BuildContext ctx, Offset globalPos, int row) {
    final onAction = widget.onHeaderAction;
    if (onAction == null) return;
    showMenu<SheetHeaderAction>(
      context: ctx,
      color: AppColors.bgSurfaceElevated,
      position: RelativeRect.fromLTRB(
        globalPos.dx,
        globalPos.dy,
        globalPos.dx + 1,
        globalPos.dy + 1,
      ),
      items: [
        _menuItem(SheetHeaderAction.insertAbove, 'Insert above'),
        _menuItem(SheetHeaderAction.insertBelow, 'Insert below'),
        _menuItem(SheetHeaderAction.deleteRow, 'Delete row'),
        _menuItem(SheetHeaderAction.clearRow, 'Clear row'),
      ],
    ).then((action) {
      if (action != null) onAction(action, row);
    });
  }

  PopupMenuItem<SheetHeaderAction> _menuItem(
      SheetHeaderAction action, String label) {
    return PopupMenuItem<SheetHeaderAction>(
      value: action,
      child: Text(label, style: AppText.body.copyWith(fontSize: 14)),
    );
  }

  Widget _gutter(String text, double width, {double? height}) => Container(
        width: width,
        height: height ?? _headerH,
        alignment: Alignment.center,
        decoration: const BoxDecoration(
          color: AppColors.bgSurfaceElevated,
          border: Border(
            right: BorderSide(color: AppColors.borderSubtle),
            bottom: BorderSide(color: AppColors.borderSubtle),
          ),
        ),
        child: Text(text,
            style: AppText.caption.copyWith(color: AppColors.textMuted)),
      );

  // ── Cell ─────────────────────────────────────────────────────────────────────

  Widget _cell(int r, int c) {
    final colW = _colWidth(c);
    final rowH = _rowHeight(r);
    final sel = widget.sel;
    final inRange = sel != null && sel.range.contains(r, c);
    final isAnchor =
        sel != null && r == sel.anchorRow && c == sel.anchorCol;

    final univCell = widget.sheet.cellAt(r, c);
    final style = widget.sheet.resolveStyle(r, c);

    final displayText = style.numFmt != null
        ? formatNumber(univCell.value, style.numFmt)
        : univCell.display;

    final isFrozenHeader = widget.sheet.frozen && (r == 0 || c == 0);

    Color bgColor;
    if (isFrozenHeader && style.bgColor == null && !inRange) {
      bgColor = AppColors.bgSurfaceElevated;
    } else if (style.bgColor != null) {
      bgColor = parseHex(style.bgColor!) ?? AppColors.bgSurface;
    } else if (inRange) {
      bgColor = AppColors.accent.withValues(alpha: 0.16);
    } else {
      bgColor = AppColors.bgSurface;
    }

    final Border border;
    if (isAnchor) {
      border = Border.all(color: AppColors.accent, width: 1.5);
    } else if (inRange) {
      border = Border.all(
        color: AppColors.accent.withValues(alpha: 0.5),
        width: 0.75,
      );
    } else if (isFrozenHeader) {
      border = Border(
        right: BorderSide(
          color: AppColors.borderDefault,
          width: r == 0 ? 0.5 : 2.0,
        ),
        bottom: BorderSide(
          color: AppColors.borderDefault,
          width: r == 0 ? 2.0 : 0.5,
        ),
        left: const BorderSide(color: AppColors.borderSubtle, width: 0.5),
        top: const BorderSide(color: AppColors.borderSubtle, width: 0.5),
      );
    } else {
      border = Border.all(color: AppColors.borderSubtle, width: 0.5);
    }

    final hasLink = widget.sheet.linkAt(r, c) != null;

    final textColor = hasLink
        ? AppColors.accent
        : style.color != null
            ? (parseHex(style.color!) ?? AppColors.textPrimary)
            : AppColors.textPrimary;

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
        height: rowH,
        alignment: alignment,
        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.sm),
        // ClipRect guarantees a cell's text NEVER bleeds into its neighbours,
        // even mid-wrap or for a very long unbroken token — content is contained.
        clipBehavior: Clip.hardEdge,
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
