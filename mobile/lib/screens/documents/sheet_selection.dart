/// Pure selection model for the native sheet editor.
///
/// No Flutter imports beyond `dart:ui` (for [Offset]). All geometry is
/// expressed in grid coordinates (row, col) — the widget layer is responsible
/// for translating between pixel space and grid space.
library;

import 'dart:ui' show Offset;

import 'package:lazyclaw_mobile/screens/documents/univer_model.dart';

// ── SheetSelection ────────────────────────────────────────────────────────────

/// An immutable anchor+focus selection over a grid.
///
/// The *anchor* is the cell where the selection started (tap / long-press).
/// The *focus* is the cell most recently touched (the cell the handle was
/// dragged to, or the same cell as anchor for a single-cell tap).
///
/// The normalized [range] is always valid regardless of which corner the
/// anchor is in relative to the focus.
class SheetSelection {
  const SheetSelection(
    this.anchorRow,
    this.anchorCol,
    this.focusRow,
    this.focusCol,
  );

  /// Single-cell convenience constructor.
  factory SheetSelection.single(int r, int c) =>
      SheetSelection(r, c, r, c);

  final int anchorRow;
  final int anchorCol;
  final int focusRow;
  final int focusCol;

  /// The normalized rectangular range covering anchor → focus.
  SelRange get range => SelRange(anchorRow, anchorCol, focusRow, focusCol);

  /// True when anchor == focus (a single cell is selected).
  bool get isSingle => anchorRow == focusRow && anchorCol == focusCol;

  /// Keep the anchor fixed; move the focus to (r, c).
  SheetSelection extendTo(int r, int c) =>
      SheetSelection(anchorRow, anchorCol, r, c);

  @override
  bool operator ==(Object other) =>
      other is SheetSelection &&
      anchorRow == other.anchorRow &&
      anchorCol == other.anchorCol &&
      focusRow == other.focusRow &&
      focusCol == other.focusCol;

  @override
  int get hashCode => Object.hash(anchorRow, anchorCol, focusRow, focusCol);

  @override
  String toString() =>
      'SheetSelection(anchor:$anchorRow,$anchorCol focus:$focusRow,$focusCol)';
}

// ── Hit-test helper ───────────────────────────────────────────────────────────

/// Map a viewport-local pixel [local] to a (row, col) grid coordinate.
///
/// Returns `(-1, col)` when the tap lands in the column-header row,
/// `(row, -1)` when it lands in the row-gutter column, and
/// `(-1, -1)` for the top-left gutter corner.
///
/// Negative (out-of-grid) positions clamp to row/col 0.
///
/// Parameters:
///   - [gutterW]  width of the row-number gutter (pixels)
///   - [headerH]  height of the column-header row (pixels)
///   - [colW]     width of a single data column (pixels; all columns equal)
///   - [rowH]     height of a single data row (pixels; all rows equal)
(int row, int col) cellFromOffset(
  Offset local, {
  required double gutterW,
  required double headerH,
  required double colW,
  required double rowH,
}) {
  final x = local.dx;
  final y = local.dy;

  // Column header zone (above data rows).
  if (y < headerH) {
    if (x < gutterW) return (-1, -1);
    final col = ((x - gutterW) / colW).floor();
    return (-1, col < 0 ? 0 : col);
  }

  // Row gutter zone (left of data columns).
  if (x < gutterW) {
    final row = ((y - headerH) / rowH).floor();
    return (row < 0 ? 0 : row, -1);
  }

  final row = ((y - headerH) / rowH).floor();
  final col = ((x - gutterW) / colW).floor();
  return (row < 0 ? 0 : row, col < 0 ? 0 : col);
}
