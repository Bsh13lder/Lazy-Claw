"""Insert and delete whole rows / columns.

Pure dict-shaping, no I/O, every mutator returns a NEW snapshot.

## What this does — and pointedly does not — handle

Shifting a row index touches five places, and this module handles all five:
``cellData`` keys, ``rowData``/``columnData`` keys, ``mergeData`` rectangles,
the hyperlink resource's per-cell entries, and ``rowCount``/``columnCount``.

It does **NOT** rewrite formula references. Insert a row above ``=SUM(A1:A10)``
and that formula still says ``A1:A10`` where a spreadsheet would have made it
``A1:A11``. Doing it properly needs a real A1 tokeniser that respects string
literals (``="A1"``), sheet-qualified refs (``Sheet2!A1``), absolute markers,
and functions that defeat static rewriting outright (``INDIRECT``, ``OFFSET``).
Getting that 90% right is WORSE than not doing it: a silently-wrong
``=SUM(A1:A9)`` is undetectable by the user, whereas "references were not
adjusted" is a caveat they can act on. Same for the plugin resources
(conditional formatting, data validation, notes) — opaque JSON keyed by range
that we can't shift without parsing each plugin's private schema.

So: the skill wrapping this says so in its result, and this is deliberately
**not** exposed to the ✨ edit plan, where the model would reach for it
unprompted. The web editor and the Flutter app both do the whole job correctly
on the surfaces where people actually restructure a sheet.
"""

from __future__ import annotations

import copy
from typing import Any

from lazyclaw.sheets import snapshot as S

#: One call may not move more than this many rows/columns — a backstop, not a
#: real limit; restructuring never approaches it.
MAX_SHIFT = 1000

_LINK_RESOURCE = "SHEET_HYPER_LINK_PLUGIN"


def _shift_keyed(data: Any, at: int, delta: int) -> dict[str, Any]:
    """Shift the integer-string keys of a Univer map at/after ``at`` by ``delta``.

    Deletion (negative ``delta``) drops the keys inside the removed band.
    """
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in data.items():
        try:
            index = int(key)
        except (TypeError, ValueError):
            out[key] = value
            continue
        if index < at:
            out[key] = value
        elif delta < 0 and index < at - delta:
            continue  # inside the deleted band
        else:
            out[str(index + delta)] = value
    return out


def _shift_cells(cell_data: Any, at: int, delta: int, *, axis: str) -> dict[str, Any]:
    """Shift ``cellData`` along ``axis`` ('row' or 'col')."""
    if not isinstance(cell_data, dict):
        return {}
    if axis == "row":
        return _shift_keyed(cell_data, at, delta)
    return {
        r_key: _shift_keyed(cols, at, delta)
        for r_key, cols in cell_data.items()
        if isinstance(cols, dict)
    }


def _shift_merges(
    merges: Any, at: int, delta: int, *, axis: str
) -> list[dict[str, int]]:
    """Shift merge rects, growing one that STRADDLES the insertion point.

    A rect entirely inside a deleted band is dropped; a rect that only partly
    overlaps is clamped to what survives, and collapses away if that leaves it
    a single cell.
    """
    start_key = "startRow" if axis == "row" else "startColumn"
    end_key = "endRow" if axis == "row" else "endColumn"
    out: list[dict[str, int]] = []
    for rect in merges or []:
        if not isinstance(rect, dict):
            continue
        try:
            values = {k: int(rect[k]) for k in
                      ("startRow", "startColumn", "endRow", "endColumn")}
        except (KeyError, TypeError, ValueError):
            continue
        start, end = values[start_key], values[end_key]
        if delta > 0:
            if start >= at:
                start += delta
                end += delta
            elif end >= at:
                end += delta  # straddles the insertion — the merge grows
        else:
            band_end = at - delta - 1  # inclusive last removed index
            if start >= at and end <= band_end:
                continue  # entirely removed
            if start > band_end:
                start += delta
                end += delta
            else:
                if end > band_end:
                    end += delta
                if start >= at:
                    start = at
                end = max(end, start)
        if end <= start and values[end_key] > values[start_key]:
            continue  # collapsed to nothing meaningful
        values[start_key], values[end_key] = start, end
        out.append(values)
    return out


def _shift_links(snap: dict[str, Any], sid: str, at: int, delta: int, *, axis: str) -> None:
    """Shift the hyperlink resource's per-cell entries, in place on ``snap``.

    Without this every link below an inserted row points at the wrong cell —
    the links are stored as their own resource, entirely outside ``cellData``.
    """
    resources = snap.get("resources")
    if not isinstance(resources, list):
        return
    key = "row" if axis == "row" else "column"
    for res in resources:
        if not isinstance(res, dict) or res.get("name") != _LINK_RESOURCE:
            continue
        import json

        try:
            payload = json.loads(res.get("data") or "{}")
        except (TypeError, ValueError):
            continue
        entries = payload.get(sid)
        if not isinstance(entries, list):
            continue
        kept = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get(key), int):
                kept.append(entry)
                continue
            index = entry[key]
            if index < at:
                kept.append(entry)
            elif delta < 0 and index < at - delta:
                continue  # the cell it pointed at is gone
            else:
                kept.append({**entry, key: index + delta})
        payload[sid] = kept
        res["data"] = json.dumps(payload)


def _apply(
    snap: dict[str, Any], at: int, delta: int, *, axis: str, sheet: int | str
) -> dict[str, Any]:
    if at < 0:
        raise ValueError(f"index must be >= 0, got {at}")
    if not delta or abs(delta) > MAX_SHIFT:
        raise ValueError(f"count must be 1..{MAX_SHIFT}")

    out = copy.deepcopy(snap)
    sid = S.resolve_sheet_id(out, sheet)
    sheet_obj = out["sheets"][sid]

    sheet_obj["cellData"] = _shift_cells(
        sheet_obj.get("cellData"), at, delta, axis=axis
    )
    bucket = "rowData" if axis == "row" else "columnData"
    if isinstance(sheet_obj.get(bucket), dict):
        shifted = _shift_keyed(sheet_obj[bucket], at, delta)
        if shifted:
            sheet_obj[bucket] = shifted
        else:
            sheet_obj.pop(bucket, None)
    merges = _shift_merges(sheet_obj.get("mergeData"), at, delta, axis=axis)
    if merges:
        sheet_obj["mergeData"] = merges
    else:
        sheet_obj.pop("mergeData", None)

    _shift_links(out, sid, at, delta, axis=axis)

    count_key = "rowCount" if axis == "row" else "columnCount"
    current = sheet_obj.get(count_key)
    if isinstance(current, int):
        sheet_obj[count_key] = max(1, current + delta)
    return out


def insert_rows(
    snap: dict[str, Any], at: int, count: int = 1, sheet: int | str = 0
) -> dict[str, Any]:
    """Insert ``count`` blank rows ABOVE 0-based row ``at``.

    Formula references are NOT adjusted — see the module docstring.
    """
    return _apply(snap, at, count, axis="row", sheet=sheet)


def delete_rows(
    snap: dict[str, Any], at: int, count: int = 1, sheet: int | str = 0
) -> dict[str, Any]:
    """Delete ``count`` rows starting at 0-based row ``at``."""
    return _apply(snap, at, -count, axis="row", sheet=sheet)


def insert_columns(
    snap: dict[str, Any], at: int, count: int = 1, sheet: int | str = 0
) -> dict[str, Any]:
    """Insert ``count`` blank columns to the LEFT of 0-based column ``at``."""
    return _apply(snap, at, count, axis="col", sheet=sheet)


def delete_columns(
    snap: dict[str, Any], at: int, count: int = 1, sheet: int | str = 0
) -> dict[str, Any]:
    """Delete ``count`` columns starting at 0-based column ``at``."""
    return _apply(snap, at, -count, axis="col", sheet=sheet)


def has_formulas(snap: dict[str, Any], sheet: int | str = 0) -> bool:
    """Whether the worksheet contains any formula.

    Callers use this to decide whether the "references were not adjusted"
    caveat is worth showing — on a sheet of plain values it is just noise.
    """
    return any(cell.get("f") for _, _, cell in S.iter_cells(snap, sheet))
