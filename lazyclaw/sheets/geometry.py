"""Worksheet geometry: column widths, row heights, merges, freeze panes.

Pure dict-shaping, no I/O, every mutator returns a NEW snapshot.

Formatting alone doesn't make a table readable — a bold header above 88-pixel
columns is still a wall of truncated text, because the mobile grid ellipsises
anything that doesn't fit and the web editor scrolls it out of view. This module
is the other half of "readable": size the columns to their content, give the
header a frozen row, merge a title across the table.

Keys written here (all verified against ``@univerjs/core`` 0.24's
``IWorksheetData``, and all read by both the web editor and the Flutter grid):

=================  ==========================================================
``columnData``     ``{"<col>": {"w": px}}`` — string-keyed, like ``cellData``
``rowData``        ``{"<row>": {"h": px}}``
``mergeData``      ``[{startRow, startColumn, endRow, endColumn}]``, INCLUSIVE
``freeze``         ``{xSplit, ySplit, startRow, startColumn}``
=================  ==========================================================

Row/column INSERT and DELETE deliberately live elsewhere: doing them correctly
means rewriting relative formula references and shifting opaque plugin-resource
ranges, and a half-correct implementation corrupts sheets invisibly. The web
editor and the Flutter app both do it properly on the surfaces where users
actually restructure a sheet.
"""

from __future__ import annotations

import copy
from typing import Any

from lazyclaw.sheets import snapshot as S
from lazyclaw.sheets import styles as ST

# ───────────────────────── constants ────────────────────────────────

#: ``IWorksheetData.defaultColumnWidth`` / ``defaultRowHeight``. NOTE the row
#: height is 24, not the Flutter grid's 36 — that 36 is a touch-target size, and
#: writing it would make every agent-set row taller than the web editor's.
DEFAULT_COL_WIDTH = 88
DEFAULT_ROW_HEIGHT = 24

COL_WIDTH_MIN, COL_WIDTH_MAX = 8.0, 2000.0
ROW_HEIGHT_MIN, ROW_HEIGHT_MAX = 8.0, 1000.0

#: Auto-fit geometry, mirroring ``autoFitColWidth`` in
#: ``mobile/lib/screens/documents/sheet_link_ui.dart``. Pure Python can't
#: measure glyphs, so a per-character estimate stands in for its ``TextPainter``:
#: 7 px is both the average advance of the grid's 13 px UI font and Excel's
#: Calibri-11 max-digit-width, so the same constant also serves the xlsx bridge.
AUTOFIT_PX_PER_CHAR = 7.0
AUTOFIT_PADDING_PX = 16.0
AUTOFIT_MIN_PX = 56.0
AUTOFIT_MAX_PX = 320.0
AUTOFIT_MAX_SCAN_ROWS = 2000

#: Frozen rows/columns are a viewport affordance; nobody freezes 500 rows.
FREEZE_MAX = 100

_MERGE_KEYS = ("startRow", "startColumn", "endRow", "endColumn")


# ───────────────────────── helpers ──────────────────────────────────

def _sheet_obj(snap: dict[str, Any], sheet: int | str) -> dict[str, Any]:
    return snap["sheets"][S.resolve_sheet_id(snap, sheet)]


def _clamp(value: Any, low: float, high: float, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def _axis_entry(sheet_obj: dict[str, Any], bucket: str, index: int) -> dict[str, Any]:
    """The ``columnData``/``rowData`` entry for ``index``, created if absent.

    Merges rather than replaces, so a sibling ``hd`` (hidden), ``s`` (style) or
    ``custom`` on that row/column survives a resize.
    """
    data = sheet_obj.get(bucket)
    if not isinstance(data, dict):
        data = {}
        sheet_obj[bucket] = data
    entry = data.get(str(index))
    if not isinstance(entry, dict):
        entry = {}
        data[str(index)] = entry
    return entry


def _axis_value(
    snap: dict[str, Any], sheet: int | str, bucket: str, key: str,
    index: int, default: float,
) -> float:
    sheet_obj = _sheet_obj(snap, sheet)
    entry = (sheet_obj.get(bucket) or {}).get(str(index))
    if isinstance(entry, dict) and isinstance(entry.get(key), (int, float)):
        return float(entry[key])
    return default


# ───────────────────────── widths & heights ─────────────────────────

def get_column_width(snap: dict[str, Any], col: int, sheet: int | str = 0) -> float:
    """Stored width of ``col`` in pixels, else Univer's default (88)."""
    return _axis_value(snap, sheet, "columnData", "w", col, DEFAULT_COL_WIDTH)


def get_row_height(snap: dict[str, Any], row: int, sheet: int | str = 0) -> float:
    """Stored height of ``row`` in pixels, else Univer's default (24)."""
    return _axis_value(snap, sheet, "rowData", "h", row, DEFAULT_ROW_HEIGHT)


def set_column_widths(
    snap: dict[str, Any], widths: dict[int, float], sheet: int | str = 0
) -> dict[str, Any]:
    """Set several column widths in one copy. Returns a NEW snapshot."""
    out = copy.deepcopy(snap)
    if not widths:
        return out
    sheet_obj = _sheet_obj(out, sheet)
    for col, width in widths.items():
        if int(col) < 0:
            continue
        entry = _axis_entry(sheet_obj, "columnData", int(col))
        entry["w"] = _clamp(width, COL_WIDTH_MIN, COL_WIDTH_MAX, DEFAULT_COL_WIDTH)
    return out


def set_column_width(
    snap: dict[str, Any], col: int, width: float, sheet: int | str = 0
) -> dict[str, Any]:
    return set_column_widths(snap, {col: width}, sheet)


def set_row_heights(
    snap: dict[str, Any], heights: dict[int, float], sheet: int | str = 0
) -> dict[str, Any]:
    """Set several row heights in one copy. Returns a NEW snapshot."""
    out = copy.deepcopy(snap)
    if not heights:
        return out
    sheet_obj = _sheet_obj(out, sheet)
    for row, height in heights.items():
        if int(row) < 0:
            continue
        entry = _axis_entry(sheet_obj, "rowData", int(row))
        entry["h"] = _clamp(height, ROW_HEIGHT_MIN, ROW_HEIGHT_MAX, DEFAULT_ROW_HEIGHT)
        # Univer ignores an explicit `h` while self-adaptive height is on.
        entry.pop("ia", None)
        entry.pop("ah", None)
    return out


def set_row_height(
    snap: dict[str, Any], row: int, height: float, sheet: int | str = 0
) -> dict[str, Any]:
    return set_row_heights(snap, {row: height}, sheet)


# ───────────────────────── auto-fit ─────────────────────────────────

def width_for_text_length(chars: int) -> float:
    """Pixel width for ``chars`` characters at the grid font, clamped."""
    if chars <= 0:
        return DEFAULT_COL_WIDTH
    raw = chars * AUTOFIT_PX_PER_CHAR + AUTOFIT_PADDING_PX
    return max(AUTOFIT_MIN_PX, min(AUTOFIT_MAX_PX, raw))


def _spans_columns(merges: list[dict[str, int]], row: int, col: int) -> bool:
    """Whether (row, col) belongs to a merge covering MORE THAN ONE column.

    Such a cell's text is painted across several columns, so no single column
    is responsible for fitting it — measuring it would let a merged three-column
    banner blow column A out to its maximum width. This is what Excel's
    auto-fit does too.

    A single-column merge (a vertical ``A1:A3``) is NOT skipped: its text really
    does have to fit inside that one column.
    """
    for rect in merges:
        if (rect["startRow"] <= row <= rect["endRow"]
                and rect["startColumn"] <= col <= rect["endColumn"]):
            return rect["endColumn"] > rect["startColumn"]
    return False


def measure_column_width(
    snap: dict[str, Any], col: int, sheet: int | str = 0
) -> float:
    """The width ``col`` needs for its widest DISPLAYED cell. Pure — no write.

    Measures the formatted text (``1234.5678`` under ``#,##0.00`` is
    ``1,234.57``, 8 chars not 9), skips cells covered by someone else's merge,
    and returns the Univer default for an empty column.
    """
    max_row, _ = S.used_bounds(snap, sheet)
    if max_row < 0:
        return DEFAULT_COL_WIDTH
    merges = merged_ranges(snap, sheet)
    widest = 0
    for row in range(min(max_row, AUTOFIT_MAX_SCAN_ROWS - 1) + 1):
        cell = S.get_cell(snap, row, col, sheet)
        if not S.has_content(cell):
            continue
        if _spans_columns(merges, row, col):
            continue
        pattern = (ST.resolve_style(snap, row, col, sheet).get("n") or {}).get(
            "pattern"
        )
        text = ST.format_number(S.cell_display(cell), pattern)
        widest = max(widest, len(text))
    return width_for_text_length(widest)


def auto_fit_columns(
    snap: dict[str, Any], cols: list[int] | None = None, sheet: int | str = 0
) -> dict[str, Any]:
    """Size ``cols`` (default: every used column) to their content.

    Columns past the used range are skipped — sizing an empty column to the
    default would just write noise into the snapshot.
    """
    _, max_col = S.used_bounds(snap, sheet)
    if max_col < 0:
        return copy.deepcopy(snap)
    targets = range(max_col + 1) if cols is None else cols
    widths = {
        col: measure_column_width(snap, col, sheet)
        for col in targets
        if 0 <= col <= max_col
    }
    return set_column_widths(snap, widths, sheet)


# ───────────────────────── merges ───────────────────────────────────

def merged_ranges(snap: dict[str, Any], sheet: int | str = 0) -> list[dict[str, int]]:
    """The worksheet's merge rects, normalised and validated."""
    raw = _sheet_obj(snap, sheet).get("mergeData")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, int]] = []
    for rect in raw:
        if not isinstance(rect, dict):
            continue
        try:
            values = {k: int(rect[k]) for k in _MERGE_KEYS}
        except (KeyError, TypeError, ValueError):
            continue
        out.append(values)
    return out


def _overlaps(a: dict[str, int], b: dict[str, int]) -> bool:
    return not (
        a["endRow"] < b["startRow"] or a["startRow"] > b["endRow"]
        or a["endColumn"] < b["startColumn"] or a["startColumn"] > b["endColumn"]
    )


def merge_cells(
    snap: dict[str, Any], r1: int, c1: int, r2: int, c2: int,
    sheet: int | str = 0,
) -> dict[str, Any]:
    """Merge a rectangle. End indices are INCLUSIVE.

    Any existing merge overlapping the new one is dropped first — Univer
    rejects overlapping rects. The covered cells keep their values (Univer just
    hides them), so an unmerge brings the data back.
    """
    lo_r, hi_r = sorted((int(r1), int(r2)))
    lo_c, hi_c = sorted((int(c1), int(c2)))
    if lo_r < 0 or lo_c < 0:
        raise ValueError(f"merge range must be >= 0, got ({r1},{c1})-({r2},{c2})")

    rect = {
        "startRow": lo_r, "startColumn": lo_c, "endRow": hi_r, "endColumn": hi_c,
    }
    out = copy.deepcopy(snap)
    kept = [r for r in merged_ranges(out, sheet) if not _overlaps(r, rect)]
    _sheet_obj(out, sheet)["mergeData"] = kept + [rect]
    return out


def merged_range_at(
    snap: dict[str, Any], row: int, col: int, sheet: int | str = 0
) -> dict[str, int] | None:
    """The merge rect containing (row, col), or ``None``."""
    for rect in merged_ranges(snap, sheet):
        if (rect["startRow"] <= row <= rect["endRow"]
                and rect["startColumn"] <= col <= rect["endColumn"]):
            return rect
    return None


def unmerge_cells(
    snap: dict[str, Any], row: int, col: int, sheet: int | str = 0
) -> dict[str, Any]:
    """Dissolve the merge CONTAINING (row, col).

    Matching by containment, not equality: a user saying "unmerge B1" means the
    A1:C1 merge that B1 sits inside.
    """
    out = copy.deepcopy(snap)
    kept = [
        rect for rect in merged_ranges(out, sheet)
        if not (rect["startRow"] <= row <= rect["endRow"]
                and rect["startColumn"] <= col <= rect["endColumn"])
    ]
    _sheet_obj(out, sheet)["mergeData"] = kept
    return out


# ───────────────────────── freeze panes ─────────────────────────────

def freeze_panes(
    snap: dict[str, Any], rows: int = 0, cols: int = 0, sheet: int | str = 0
) -> dict[str, Any]:
    """Keep the top ``rows`` and left ``cols`` visible while scrolling.

    ``IFreeze``: ``ySplit``/``xSplit`` are the frozen counts and
    ``startRow``/``startColumn`` the first scrollable index — the same numbers.
    Both zero REMOVES the key rather than writing zeros, which is how the web
    editor and the Flutter app both express "not frozen".
    """
    rows = max(0, min(FREEZE_MAX, int(rows or 0)))
    cols = max(0, min(FREEZE_MAX, int(cols or 0)))
    out = copy.deepcopy(snap)
    sheet_obj = _sheet_obj(out, sheet)
    if rows == 0 and cols == 0:
        sheet_obj.pop("freeze", None)
        return out
    sheet_obj["freeze"] = {
        "xSplit": cols, "ySplit": rows, "startRow": rows, "startColumn": cols,
    }
    return out


def unfreeze(snap: dict[str, Any], sheet: int | str = 0) -> dict[str, Any]:
    """Remove any freeze pane."""
    return freeze_panes(snap, rows=0, cols=0, sheet=sheet)


def frozen_counts(snap: dict[str, Any], sheet: int | str = 0) -> tuple[int, int]:
    """``(rows, cols)`` currently frozen — ``(0, 0)`` when not frozen."""
    freeze = _sheet_obj(snap, sheet).get("freeze")
    if not isinstance(freeze, dict):
        return 0, 0
    rows = freeze.get("ySplit") or 0
    cols = freeze.get("xSplit") or 0
    return int(rows), int(cols)
