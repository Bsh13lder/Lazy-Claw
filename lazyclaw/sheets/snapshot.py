"""Pure helpers over Univer's ``IWorkbookData`` snapshot.

A LazyClaw sheet is stored as one JSON blob in Univer's native workbook
format. These functions read and **immutably** edit that structure so the
agent skills (:mod:`lazyclaw.skills.builtin.sheets`) and the xlsx layer
(:mod:`lazyclaw.sheets.xlsx_io`) share a single cell model. No I/O, no Univer
runtime dependency — just dict shaping.

Univer snapshot shape (only the parts we touch)::

    {
      "id": "wb-…", "name": "Budget", "locale": "enUS", "styles": {},
      "sheetOrder": ["sh-…"],
      "sheets": {
        "sh-…": {
          "id": "sh-…", "name": "Sheet1",
          "rowCount": 1000, "columnCount": 20,
          "cellData": {"0": {"0": {"v": 10, "f": "=SUM(A1:A2)"}}}
        }
      }
    }

Cell objects (Univer ``ICellData``) use ``v`` = value, ``f`` = formula string.
``cellData`` is keyed by *string* row index → *string* col index, matching
Univer's on-the-wire JSON. Every mutator returns a NEW snapshot (deepcopy) —
callers never observe in-place changes (project immutability rule).
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Iterator
from uuid import uuid4

# Univer's defaults for a fresh worksheet.
DEFAULT_ROWS = 1000
DEFAULT_COLS = 20

_A1_RE = re.compile(r"^\$?([A-Za-z]+)\$?([0-9]+)$")


# ───────────────────────── A1 ⇄ (row, col) ──────────────────────────

def col_to_letter(col: int) -> str:
    """0-based column index → spreadsheet letters (0→A, 25→Z, 26→AA)."""
    if col < 0:
        raise ValueError(f"column index must be >= 0, got {col}")
    letters = ""
    n = col + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def letter_to_col(letters: str) -> int:
    """Spreadsheet letters → 0-based column index (A→0, Z→25, AA→26)."""
    if not letters or not letters.isalpha():
        raise ValueError(f"invalid column letters: {letters!r}")
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def rc_to_a1(row: int, col: int) -> str:
    """0-based (row, col) → A1 reference (0,0 → 'A1')."""
    return f"{col_to_letter(col)}{row + 1}"


def a1_to_rc(ref: str) -> tuple[int, int]:
    """A1 reference → 0-based (row, col). 'B3' → (2, 1)."""
    m = _A1_RE.match(ref.strip())
    if not m:
        raise ValueError(f"invalid A1 reference: {ref!r}")
    return int(m.group(2)) - 1, letter_to_col(m.group(1))


# ───────────────────────── value coercion ───────────────────────────

def coerce_value(value: Any) -> Any:
    """Best-effort numeric coercion for a cell value.

    Numbers/bools pass through (bool checked before int — ``bool`` is an
    ``int`` subclass). Numeric-looking strings become int/float so formulas
    and exports treat them as numbers. Everything else stays as-is.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s == "":
            return ""
        try:
            return int(s)
        except ValueError:
            pass
        try:
            return float(s)
        except ValueError:
            return value
    return value


# ───────────────────────── workbook factory ─────────────────────────

def blank_workbook(
    name: str,
    *,
    rows: int = DEFAULT_ROWS,
    cols: int = DEFAULT_COLS,
    workbook_id: str | None = None,
    sheet_id: str | None = None,
    sheet_name: str = "Sheet1",
) -> dict[str, Any]:
    """Build a minimal valid empty Univer ``IWorkbookData``.

    IDs default to fresh uuids; tests may pass fixed ones for determinism.
    """
    wb_id = workbook_id or f"wb-{uuid4().hex[:12]}"
    sh_id = sheet_id or f"sh-{uuid4().hex[:12]}"
    return {
        "id": wb_id,
        "name": (name or "Untitled sheet").strip() or "Untitled sheet",
        "appVersion": "0.1.0",
        "locale": "enUS",
        "styles": {},
        "sheetOrder": [sh_id],
        "sheets": {
            sh_id: {
                "id": sh_id,
                "name": sheet_name,
                "rowCount": rows,
                "columnCount": cols,
                "cellData": {},
            }
        },
    }


# ───────────────────────── sheet resolution ─────────────────────────

def _resolve_sheet_id(snap: dict[str, Any], sheet: int | str = 0) -> str:
    """Resolve a sheet selector to a worksheet id key.

    ``sheet`` may be an int index into ``sheetOrder``, a sheet *id*, or a
    sheet *name* (case-insensitive). Raises ``KeyError`` / ``IndexError`` on
    a bad selector so callers fail fast.
    """
    order = snap.get("sheetOrder") or list((snap.get("sheets") or {}).keys())
    if not order:
        raise KeyError("workbook has no sheets")
    if isinstance(sheet, int):
        return order[sheet]
    sheets = snap.get("sheets") or {}
    if sheet in sheets:
        return sheet
    for sid, sdata in sheets.items():
        if str(sdata.get("name", "")).lower() == sheet.lower():
            return sid
    raise KeyError(f"no sheet named or id'd {sheet!r}")


def sheet_names(snap: dict[str, Any]) -> list[str]:
    """Worksheet names in tab order."""
    sheets = snap.get("sheets") or {}
    return [
        str(sheets.get(sid, {}).get("name", sid))
        for sid in (snap.get("sheetOrder") or list(sheets.keys()))
    ]


# ───────────────────────── cell read ────────────────────────────────

def get_cell(
    snap: dict[str, Any], row: int, col: int, sheet: int | str = 0
) -> dict[str, Any] | None:
    """Return the raw ``ICellData`` dict at (row, col), or ``None`` if empty."""
    sid = _resolve_sheet_id(snap, sheet)
    cell_data = snap["sheets"][sid].get("cellData") or {}
    return (cell_data.get(str(row)) or {}).get(str(col))


def cell_display(cell: dict[str, Any] | None) -> Any:
    """The value a reader should see: ``v`` if present, else ``f``, else ''."""
    if not cell:
        return ""
    if cell.get("v") is not None:
        return cell["v"]
    if cell.get("f"):
        return cell["f"]
    return ""


def iter_cells(
    snap: dict[str, Any], sheet: int | str = 0
) -> Iterator[tuple[int, int, dict[str, Any]]]:
    """Yield ``(row, col, cell)`` for non-empty cells, row-major sorted."""
    sid = _resolve_sheet_id(snap, sheet)
    cell_data = snap["sheets"][sid].get("cellData") or {}
    for r in sorted(cell_data, key=lambda x: int(x)):
        cols = cell_data[r] or {}
        for c in sorted(cols, key=lambda x: int(x)):
            cell = cols[c]
            if cell:
                yield int(r), int(c), cell


def used_bounds(snap: dict[str, Any], sheet: int | str = 0) -> tuple[int, int]:
    """``(max_row, max_col)`` (inclusive, 0-based) of populated cells.

    Returns ``(-1, -1)`` for an empty sheet.
    """
    max_r = max_c = -1
    for r, c, _ in iter_cells(snap, sheet):
        max_r = max(max_r, r)
        max_c = max(max_c, c)
    return max_r, max_c


def as_grid(snap: dict[str, Any], sheet: int | str = 0) -> list[list[Any]]:
    """Dense 2-D grid of display values up to the used bounds.

    Empty for a sheet with no cells. Designed for agent ``read_sheet`` output.
    """
    max_r, max_c = used_bounds(snap, sheet)
    if max_r < 0:
        return []
    grid: list[list[Any]] = [["" for _ in range(max_c + 1)] for _ in range(max_r + 1)]
    for r, c, cell in iter_cells(snap, sheet):
        grid[r][c] = cell_display(cell)
    return grid


# ───────────────────────── cell write (immutable) ───────────────────

def _apply_cell(
    sheet_obj: dict[str, Any],
    row: int,
    col: int,
    value: Any,
    formula: str | None,
    *,
    clear: bool,
) -> None:
    """Mutate *one* worksheet's cellData in place (caller owns the copy)."""
    if row < 0 or col < 0:
        raise ValueError(f"row/col must be >= 0, got ({row}, {col})")
    cell_data = sheet_obj.setdefault("cellData", {})
    r_key, c_key = str(row), str(col)

    if clear:
        if r_key in cell_data:
            cell_data[r_key].pop(c_key, None)
            if not cell_data[r_key]:
                cell_data.pop(r_key)
        return

    cell: dict[str, Any] = {}
    if formula is not None:
        f = formula if formula.startswith("=") else f"={formula}"
        cell["f"] = f
        # Keep an explicit value alongside the formula when the caller knows
        # it (e.g. recalc); otherwise leave None for the recalc / browser pass.
        if value is not None:
            cell["v"] = coerce_value(value)
    else:
        cell["v"] = coerce_value(value)
    cell_data.setdefault(r_key, {})[c_key] = cell


def set_cell(
    snap: dict[str, Any],
    row: int,
    col: int,
    value: Any = None,
    formula: str | None = None,
    sheet: int | str = 0,
) -> dict[str, Any]:
    """Return a NEW snapshot with (row, col) set.

    ``formula`` wins when given (a leading ``=`` is added if missing).
    Passing both ``value=None`` and ``formula=None`` clears the cell.
    """
    out = copy.deepcopy(snap)
    sid = _resolve_sheet_id(out, sheet)
    _apply_cell(
        out["sheets"][sid], row, col, value, formula,
        clear=(value is None and formula is None),
    )
    return out


def set_cells(
    snap: dict[str, Any],
    edits: list[dict[str, Any]],
    sheet: int | str = 0,
) -> dict[str, Any]:
    """Apply a batch of edits in one deepcopy. Returns a NEW snapshot.

    Each edit is ``{"row": int, "col": int, "value"?: Any, "formula"?: str}``.
    A1 form is also accepted: ``{"cell": "B3", ...}``.
    """
    out = copy.deepcopy(snap)
    sid = _resolve_sheet_id(out, sheet)
    sheet_obj = out["sheets"][sid]
    for e in edits:
        if "cell" in e:
            row, col = a1_to_rc(str(e["cell"]))
        else:
            row, col = int(e["row"]), int(e["col"])
        value = e.get("value")
        formula = e.get("formula")
        _apply_cell(
            sheet_obj, row, col, value, formula,
            clear=(value is None and formula is None),
        )
    return out


# ───────────────────────── hyperlink resource helpers ───────────────

_LINK_RESOURCE = "SHEET_HYPER_LINK_PLUGIN"
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_MD_LINK_RE = re.compile(r"^\[([^\]]+)\]\((https?://[^\s)]+)\)$")
_TRAIL_PUNCT = ".,;:!?)"


def _link_data(snap: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Parse the hyperlink plugin resource into a dict keyed by sheet id.

    Returns ``{}`` on missing resource or any :class:`json.JSONDecodeError`.
    """
    resources = snap.get("resources") or []
    for res in resources:
        if res.get("name") == _LINK_RESOURCE:
            try:
                return json.loads(res.get("data") or "{}")
            except json.JSONDecodeError:
                return {}
    return {}


def get_sheet_links(
    snap: dict[str, Any], sheet: int | str = 0
) -> list[dict[str, Any]]:
    """Return hyperlink entries for the resolved sheet.

    Each entry has keys ``id``, ``row``, ``column``, ``payload`` (URL).
    Returns ``[]`` when no links exist for that sheet.
    """
    sid = _resolve_sheet_id(snap, sheet)
    return list(_link_data(snap).get(sid) or [])


def _write_link_data(
    snap: dict[str, Any], data: dict[str, list[dict[str, Any]]]
) -> None:
    """Mutate a caller-owned copy of *snap* to write the link resource.

    Updates the existing ``SHEET_HYPER_LINK_PLUGIN`` entry when present,
    otherwise appends a new one.
    """
    resources: list[dict[str, Any]] = snap.setdefault("resources", [])
    for res in resources:
        if res.get("name") == _LINK_RESOURCE:
            res["data"] = json.dumps(data)
            return
    resources.append({"name": _LINK_RESOURCE, "data": json.dumps(data)})


def set_cell_link(
    snap: dict[str, Any],
    row: int,
    col: int,
    url: str,
    display: str | None = None,
    sheet: int | str = 0,
) -> dict[str, Any]:
    """Return a NEW snapshot with a hyperlink set at (row, col).

    Any existing link entry at the same cell is removed first (upsert
    semantics). When *display* is not ``None`` the cell's text value is also
    updated via :func:`_apply_cell`.
    """
    out = copy.deepcopy(snap)
    sid = _resolve_sheet_id(out, sheet)

    # Read current link data, drop any existing entry at (row, col).
    data = _link_data(out)
    entries = [e for e in (data.get(sid) or []) if not (e["row"] == row and e["column"] == col)]
    entries.append({
        "id": f"l-{uuid4().hex[:8]}",
        "row": row,
        "column": col,
        "payload": url,
    })
    data[sid] = entries
    _write_link_data(out, data)

    if display is not None:
        _apply_cell(out["sheets"][sid], row, col, display, None, clear=False)

    return out


def remove_cell_link(
    snap: dict[str, Any],
    row: int,
    col: int,
    sheet: int | str = 0,
) -> dict[str, Any]:
    """Return a NEW snapshot with the hyperlink at (row, col) removed.

    Cell text is left untouched. A no-op when there is no link at the cell.
    """
    out = copy.deepcopy(snap)
    sid = _resolve_sheet_id(out, sheet)
    data = _link_data(out)
    entries = [e for e in (data.get(sid) or []) if not (e["row"] == row and e["column"] == col)]
    data[sid] = entries
    _write_link_data(out, data)
    return out


def convert_urls_to_links(snap: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Scan all sheets and convert URL / Markdown-link cells to hyperlinks.

    Rules (applied per cell):
    - Skip cells that have a formula (``f`` key present).
    - Skip cells whose value is not a string.
    - Skip cells that already have a hyperlink entry.
    - If the cell text matches :data:`_MD_LINK_RE` exactly → create a link with
      the URL from group 2 and set the cell display text to the label (group 1).
    - Else if a URL is found by :data:`_URL_RE` *and* the matched URL
      (stripped of trailing :data:`_TRAIL_PUNCT`) equals the cell text stripped
      of the same punctuation → bare-URL cell: create a link (text stays as-is).

    Returns ``(new_snapshot, count)`` where *count* is the number of links
    added. The original snapshot is never mutated.
    """
    out = copy.deepcopy(snap)
    count = 0

    order = out.get("sheetOrder") or list((out.get("sheets") or {}).keys())
    for sid in order:
        # Re-read the linked set once per sheet before the cell loop so that
        # cells converted earlier in this pass are in the skip set.
        linked: set[tuple[int, int]] = {
            (e["row"], e["column"]) for e in (_link_data(out).get(sid) or [])
        }
        for row, col, cell in iter_cells(out, sid):
            if cell.get("f") is not None:
                continue
            v = cell.get("v")
            if not isinstance(v, str):
                continue
            if (row, col) in linked:
                continue

            md = _MD_LINK_RE.match(v.strip())
            if md:
                out = set_cell_link(out, row, col, md.group(2), display=md.group(1), sheet=sid)
                linked.add((row, col))
                count += 1
                continue

            url_m = _URL_RE.search(v)
            if url_m:
                matched_url = url_m.group(0).rstrip(_TRAIL_PUNCT)
                if matched_url == v.strip().rstrip(_TRAIL_PUNCT):
                    out = set_cell_link(out, row, col, matched_url, display=None, sheet=sid)
                    linked.add((row, col))
                    count += 1

    return out, count
