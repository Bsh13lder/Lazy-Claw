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

#: Upper bounds used to close an open-ended range (``A:A`` / ``2:2``). Excel's
#: real limits; callers clamp to the sheet's own used bounds.
MAX_ROW_INDEX = 1_048_575
MAX_COL_INDEX = 16_383

_A1_RE = re.compile(r"^\$?([A-Za-z]+)\$?([0-9]+)$")
_COL_ONLY_RE = re.compile(r"^\$?([A-Za-z]+)$")
_ROW_ONLY_RE = re.compile(r"^\$?([0-9]+)$")


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
    row = int(m.group(2)) - 1
    if row < 0:
        raise ValueError(f"A1 row numbers are 1-based, got {ref!r}")
    return row, letter_to_col(m.group(1))


def _endpoint(part: str, *, is_end: bool) -> tuple[int, int]:
    """One side of a range → ``(row, col)``.

    An open axis (the whole-column ``A:A`` and whole-row ``2:2`` forms) closes
    at 0 for the start endpoint and at the sheet maximum for the end one. ``$``
    markers are ignored: absolute vs relative is a formula concern, and a range
    address means the same cells either way.
    """
    part = part.strip()
    m = _A1_RE.match(part)
    if m:
        return int(m.group(2)) - 1, letter_to_col(m.group(1))
    m = _COL_ONLY_RE.match(part)
    if m:
        return (MAX_ROW_INDEX if is_end else 0), letter_to_col(m.group(1))
    m = _ROW_ONLY_RE.match(part)
    if m:
        row = int(m.group(1)) - 1
        if row < 0:
            raise ValueError(f"row numbers are 1-based: {part!r}")
        return row, (MAX_COL_INDEX if is_end else 0)
    raise ValueError(f"invalid range endpoint: {part!r}")


def parse_range(ref: str) -> tuple[int, int, int, int]:
    """A1 range → 0-based, END-INCLUSIVE ``(r1, c1, r2, c2)``, normalised.

    Accepts a single cell (``"B3"`` → a 1×1 range), a rectangle
    (``"A1:C5"``), the absolute forms (``"$A$1:$C$5"``), a whole column
    (``"A:A"``, ``"B:D"``) and a whole row (``"2:2"``, ``"2:4"``). Endpoints
    given in any order come back normalised so ``r1 <= r2`` and ``c1 <= c2``.

    Open-ended forms close against :data:`MAX_ROW_INDEX` /
    :data:`MAX_COL_INDEX`; callers that iterate MUST clamp to
    :func:`used_bounds` rather than walking a million rows.

    Callers taking a range from outside (a skill parameter, an LLM edit plan)
    must coerce to ``str`` first — validation of untrusted shapes belongs at
    that boundary, not in this pure helper.
    """
    parts = ref.strip().split(":")
    if len(parts) == 1:
        row, col = a1_to_rc(parts[0])
        return row, col, row, col
    if len(parts) != 2:
        raise ValueError(f"invalid range: {ref!r}")
    r1, c1 = _endpoint(parts[0], is_end=False)
    r2, c2 = _endpoint(parts[1], is_end=True)
    lo_r, hi_r = (r1, r2) if r1 <= r2 else (r2, r1)
    lo_c, hi_c = (c1, c2) if c1 <= c2 else (c2, c1)
    return lo_r, lo_c, hi_r, hi_c


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


#: Public alias — ``styles`` and ``geometry`` resolve worksheets too, and they
#: shouldn't reach into a private. Same function, no behaviour change.
resolve_sheet_id = _resolve_sheet_id


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


def has_content(cell: dict[str, Any] | None) -> bool:
    """Whether ``cell`` holds a VALUE or a FORMULA — i.e. content, not just
    presentation.

    A cell carrying only ``s`` (a style) or ``custom`` is formatting, and must
    not count towards the used range: otherwise formatting an empty header row
    or a far-off cell would grow phantom rows and columns in
    :func:`used_bounds`, :func:`as_grid`, ``read_sheet`` and the CSV export.
    """
    if not cell:
        return False
    return cell.get("v") is not None or bool(cell.get("f"))


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
    for r, c, cell in iter_cells(snap, sheet):
        # Style-only cells are formatting, not content — see `has_content`.
        if not has_content(cell):
            continue
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
        # Style-only cells don't extend `used_bounds`, so they can sit OUTSIDE
        # the grid we just sized — skip them rather than index past the end.
        if not has_content(cell):
            continue
        grid[r][c] = cell_display(cell)
    return grid


# ───────────────────────── cell write (immutable) ───────────────────

#: ``ICellData`` keys that describe the cell's CONTENT and are therefore
#: replaced wholesale by a value/formula write. Everything else on the cell —
#: ``s`` (style id or inline style) and ``custom`` (plugin/user data) — is
#: presentation or metadata and survives.
#:
#: ``p`` and ``t`` are dropped deliberately rather than preserved: Univer
#: renders ``p`` (rich text) INSTEAD of ``v``, so keeping a stale ``p`` would
#: make the new value invisible; and a stale ``t`` (``CellValueType``) would
#: mislabel it — dropping it lets Univer re-infer. ``si``/``ref``/``xf``
#: describe the shared/array formula being replaced.
_VALUE_SHAPED_KEYS = ("v", "f", "t", "p", "si", "ref", "xf")


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

    # Start from the PREVIOUS cell so its formatting survives the write, then
    # strip the value-shaped keys. Rebuilding from ``{}`` here used to wipe the
    # style a user had applied in the web editor on every agent edit.
    prev = (cell_data.get(r_key) or {}).get(c_key) or {}
    cell: dict[str, Any] = copy.deepcopy(prev)
    for stale in _VALUE_SHAPED_KEYS:
        cell.pop(stale, None)

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
    Non-dict elements in the ``resources`` list are silently skipped.
    """
    resources = snap.get("resources") or []
    for res in resources:
        if not isinstance(res, dict):
            continue
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
    otherwise appends a new one. Non-dict elements in the ``resources`` list
    are silently skipped.
    """
    resources: list[dict[str, Any]] = snap.setdefault("resources", [])
    for res in resources:
        if not isinstance(res, dict):
            continue
        if res.get("name") == _LINK_RESOURCE:
            res["data"] = json.dumps(data)
            return
    resources.append({"name": _LINK_RESOURCE, "data": json.dumps(data)})


def _apply_link(
    snap_owned: dict[str, Any],
    sid: str,
    row: int,
    col: int,
    url: str,
    display: str | None,
    linked: set[tuple[int, int]],
) -> None:
    """Mutate a caller-owned snapshot to add/replace a hyperlink entry.

    Drops any existing entry at (row, col), appends the new one, serialises
    via :func:`_write_link_data`, optionally writes the display text, and
    records (row, col) in *linked*.  All arguments beyond *snap_owned* must
    already be coerced (row/col are ints).
    """
    data = _link_data(snap_owned)
    entries = [
        e for e in (data.get(sid) or [])
        if not (e["row"] == row and e["column"] == col)
    ]
    entries.append({
        "id": f"l-{uuid4().hex[:8]}",
        "row": row,
        "column": col,
        "payload": url,
    })
    data[sid] = entries
    _write_link_data(snap_owned, data)
    if display is not None:
        _apply_cell(snap_owned["sheets"][sid], row, col, display, None, clear=False)
    linked.add((row, col))


def set_cell_link(
    snap: dict[str, Any],
    row: int | str,
    col: int | str,
    url: str,
    display: str | None = None,
    sheet: int | str = 0,
) -> dict[str, Any]:
    """Return a NEW snapshot with a hyperlink set at (row, col).

    Any existing link entry at the same cell is removed first (upsert
    semantics). When *display* is not ``None`` the cell's text value is also
    updated via :func:`_apply_cell`.  *row* and *col* are coerced to ``int``
    at entry (mirrors :func:`set_cells` behaviour).
    """
    row, col = int(row), int(col)
    out = copy.deepcopy(snap)
    sid = _resolve_sheet_id(out, sheet)
    linked: set[tuple[int, int]] = set()
    _apply_link(out, sid, row, col, url, display, linked)
    return out


def remove_cell_link(
    snap: dict[str, Any],
    row: int | str,
    col: int | str,
    sheet: int | str = 0,
) -> dict[str, Any]:
    """Return a NEW snapshot with the hyperlink at (row, col) removed.

    Cell text is left untouched. A no-op when there is no link at the cell.
    *row* and *col* are coerced to ``int`` at entry.
    """
    row, col = int(row), int(col)
    out = copy.deepcopy(snap)
    sid = _resolve_sheet_id(out, sheet)
    data = _link_data(out)
    entries = [
        e for e in (data.get(sid) or [])
        if not (e["row"] == row and e["column"] == col)
    ]
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

    Performance: one deepcopy at the top; all per-cell mutations go through
    :func:`_apply_link` which operates on the already-owned copy.  ``_link_data``
    is parsed once per sheet (not once per cell match).
    """
    out = copy.deepcopy(snap)
    count = 0

    order = out.get("sheetOrder") or list((out.get("sheets") or {}).keys())
    for sid in order:
        # Parse link data once per sheet; track newly linked cells in a set.
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
                _apply_link(out, sid, row, col, md.group(2), md.group(1), linked)
                count += 1
                continue

            url_m = _URL_RE.search(v)
            if url_m:
                matched_url = url_m.group(0).rstrip(_TRAIL_PUNCT)
                if matched_url == v.strip().rstrip(_TRAIL_PUNCT):
                    _apply_link(out, sid, row, col, matched_url, None, linked)
                    count += 1

    return out, count
