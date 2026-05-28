"""Convert between the Univer snapshot and ``.xlsx`` / ``.csv``.

The snapshot (``IWorkbookData``) is LazyClaw's source of truth; ``.xlsx`` is a
derived format produced on export and parsed on import. We write formulas as
text (``=SUM(A1:A2)``) so Excel/Google Sheets recompute them on open — see
:mod:`lazyclaw.sheets.recalc` for the server-side eval used when the agent
edits formulas without a browser pass.

openpyxl (MIT) handles both directions and preserves formulas; we read with
``data_only=False`` so formula text survives the round-trip.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, time
from typing import Any
from uuid import uuid4

from openpyxl import Workbook, load_workbook

from lazyclaw.sheets import snapshot as S

# Excel caps worksheet names at 31 chars.
_SHEET_NAME_MAX = 31


def _native_for_xlsx(value: Any) -> Any:
    """Coerce a cell value to something openpyxl can write directly."""
    if value is None or isinstance(value, (int, float, str, bool, datetime, date, time)):
        return value
    return str(value)


def snapshot_to_xlsx(snap: dict[str, Any]) -> bytes:
    """Render a Univer snapshot to ``.xlsx`` bytes (formulas kept as text)."""
    wb = Workbook()
    wb.remove(wb.active)  # drop the default empty sheet; we recreate in order

    sheets = snap.get("sheets") or {}
    order = snap.get("sheetOrder") or list(sheets.keys())
    if not order:
        wb.create_sheet("Sheet1")
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    for sid in order:
        sdata = sheets.get(sid, {})
        ws = wb.create_sheet(title=(sdata.get("name") or "Sheet")[:_SHEET_NAME_MAX])
        cell_data = sdata.get("cellData") or {}
        for r_key, cols in cell_data.items():
            for c_key, cell in (cols or {}).items():
                if not cell:
                    continue
                r, c = int(r_key) + 1, int(c_key) + 1
                formula = cell.get("f")
                if formula:
                    ws.cell(row=r, column=c,
                            value=formula if formula.startswith("=") else f"={formula}")
                elif cell.get("v") is not None:
                    ws.cell(row=r, column=c, value=_native_for_xlsx(cell["v"]))

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def xlsx_to_snapshot(data: bytes, name: str | None = None) -> dict[str, Any]:
    """Parse ``.xlsx`` bytes into a Univer snapshot (formulas preserved)."""
    wb = load_workbook(io.BytesIO(data), data_only=False)
    snap = S.blank_workbook(name or "Imported")
    snap["sheets"] = {}
    snap["sheetOrder"] = []

    for ws in wb.worksheets:
        sid = f"sh-{uuid4().hex[:12]}"
        cell_data: dict[str, dict[str, Any]] = {}
        max_r = max_c = 0
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is None:
                    continue
                r, c = cell.row - 1, cell.column - 1
                v = cell.value
                if isinstance(v, str) and v.startswith("="):
                    cd: dict[str, Any] = {"f": v}
                elif isinstance(v, (datetime, date, time)):
                    cd = {"v": v.isoformat()}
                else:
                    cd = {"v": v}
                cell_data.setdefault(str(r), {})[str(c)] = cd
                max_r, max_c = max(max_r, r), max(max_c, c)
        snap["sheets"][sid] = {
            "id": sid,
            "name": ws.title,
            "rowCount": max(max_r + 1, S.DEFAULT_ROWS),
            "columnCount": max(max_c + 1, S.DEFAULT_COLS),
            "cellData": cell_data,
        }
        snap["sheetOrder"].append(sid)

    if not snap["sheetOrder"]:
        return S.blank_workbook(name or "Imported")
    return snap


def snapshot_to_csv(snap: dict[str, Any], sheet: int | str = 0) -> str:
    """Render one worksheet's display grid to CSV text."""
    grid = S.as_grid(snap, sheet)
    out = io.StringIO()
    writer = csv.writer(out)
    for row in grid:
        writer.writerow(row)
    return out.getvalue()
