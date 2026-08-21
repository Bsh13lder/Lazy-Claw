"""Agent skills for spreadsheet FORMATTING — the readable half.

``sheets.py`` writes what a table SAYS; this writes how it READS. Two skills,
split where the parameters naturally split:

``format_cells``
    Styling: weight, colour, fill, alignment, wrap, number format, font.
    Addressed by an A1 range, because formatting is range-shaped.

``format_sheet_layout``
    Geometry: column widths, row heights, auto-fit, merges, freeze panes.
    These are almost always asked for together ("widen column A and freeze the
    header"), so they are one call rather than five.

One skill per verb would add seven near-identical descriptions to compete in
``search_tools`` and seven more lines to drift out of the specialist allowlist;
a single fat ``format_sheet`` would put style and geometry parameters in one
20-property schema, which is exactly where a model fills the wrong half.

Addresses here are **A1 and 1-based** throughout (``column: "B"``, ``row: 1``).
The 0-based ``row``/``col`` form stays confined to ``set_cells``, where it
already exists — mixing the two next to an A1 ``range`` guarantees off-by-ones.
"""

from __future__ import annotations

import logging
from typing import Any

from lazyclaw.skills.base import BaseSkill
from lazyclaw.skills.builtin.sheets import _format_grid, _resolve_sheet_id

logger = logging.getLogger(__name__)

#: Backstops against a runaway plan; real formatting never approaches these.
MAX_LAYOUT_OPS = 64


def _worksheet(params: dict) -> int | str:
    """The target worksheet: a tab name, a 0-based index, or the first tab.

    Named ``worksheet`` rather than ``sheet`` because ``sheet_id`` already means
    "which document" — two similarly-named parameters side by side is how wrong
    tool calls happen.
    """
    raw = params.get("worksheet")
    if raw is None or raw == "":
        return 0
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return raw
    text = str(raw).strip()
    return int(text) if text.isdigit() else text


async def _load(config: Any, user_id: str, params: dict):
    """Resolve + fetch the target sheet. Returns ``(sheet_id, row, None)`` or
    ``(None, None, error_message)``."""
    from lazyclaw.sheets.store import get_sheet

    sid, err = await _resolve_sheet_id(config, user_id, params.get("sheet_id"))
    if err:
        return None, None, err
    row = await get_sheet(config, user_id, sid)
    if not row:
        return None, None, "Sheet not found."
    return sid, row, None


class FormatCellsSkill(BaseSkill):
    """Apply (or clear) cell formatting over an A1 range."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "format_cells"

    @property
    def description(self) -> str:
        return (
            "Format a range of spreadsheet cells so the sheet is readable: "
            "bold/italic/underline, text colour, background fill, alignment, "
            "text wrap, number format (currency/percent/date), font and size. "
            "Use for 'bold the header row', 'make column B currency', "
            "'highlight the total yellow'. Give an A1 range like 'A1:D1'. "
            "Only the fields you pass change; pass clear:true to strip all "
            "formatting. Formatting does not change any value or formula."
        )

    @property
    def category(self) -> str:
        return "sheets"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "sheet_id": {
                    "type": "string",
                    "description": "Sheet id or name (optional — defaults to most recent)",
                },
                "worksheet": {
                    "type": ["string", "integer"],
                    "description": "Worksheet tab name or 0-based index (optional — defaults to the first tab)",
                },
                "range": {
                    "type": "string",
                    "description": "A1 range to format, e.g. 'A1:D1' for a header row, 'B2' for one cell, 'B:B' for a whole column",
                },
                "bold": {"type": "boolean"},
                "italic": {"type": "boolean"},
                "underline": {"type": "boolean"},
                "strike": {"type": "boolean"},
                "color": {
                    "type": "string",
                    "description": "Text colour — '#RRGGBB' or a name like 'red'",
                },
                "bg": {
                    "type": "string",
                    "description": "Background fill — '#RRGGBB' or a name like 'yellow'",
                },
                "align": {
                    "type": "string",
                    "enum": ["left", "center", "right"],
                },
                "valign": {
                    "type": "string",
                    "enum": ["top", "middle", "bottom"],
                },
                "wrap": {
                    "type": "boolean",
                    "description": "Wrap long text inside the cell instead of letting it spill",
                },
                "number_format": {
                    "type": "string",
                    "description": "'currency', 'percent', 'integer', 'decimal', 'date', or an Excel pattern like '#,##0.00'",
                },
                "font_size": {"type": "number", "description": "Point size, e.g. 14"},
                "font": {"type": "string", "description": "Font family, e.g. 'Arial'"},
                "clear": {
                    "type": "boolean",
                    "description": "Remove all formatting from the range instead of applying (ignores the other fields)",
                },
            },
            "required": ["range"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.sheets import snapshot as S
        from lazyclaw.sheets import styles as ST
        from lazyclaw.sheets.store import save_sheet

        ref = params.get("range")
        if not isinstance(ref, str) or not ref.strip():
            return "Provide a 'range' to format, e.g. 'A1:D1'."
        try:
            r1, c1, r2, c2 = S.parse_range(ref)
        except ValueError as e:
            return f"Could not read that range ({ref!r}): {e}"

        friendly = {k: params[k] for k in ST.FRIENDLY_KEYS if k in params}
        clearing = bool(params.get("clear"))
        if not clearing and not ST.to_univer_style(friendly):
            return (
                "No formatting to apply. Pass at least one of: "
                + ", ".join(ST.FRIENDLY_KEYS)
                + " — or clear:true to strip formatting."
            )

        sid, row, err = await _load(self._config, user_id, params)
        if err:
            return err
        assert row is not None

        sheet = _worksheet(params)
        try:
            snap = (
                ST.clear_style(row["payload"], r1, c1, r2, c2, sheet)
                if clearing
                else ST.apply_style(row["payload"], r1, c1, r2, c2, friendly, sheet)
            )
        except (KeyError, IndexError) as e:
            return f"Could not format that worksheet: {e}"

        await save_sheet(self._config, user_id, row["name"], snap, sheet_id=sid)
        what = "Cleared formatting on" if clearing else "Formatted"
        applied = "" if clearing else " (" + ", ".join(sorted(friendly)) + ")"
        return (
            f"{what} {ref.upper()} in **{row['name']}**{applied}.\n"
            f"```\n{_format_grid(snap, sheet)}\n```"
        )


class FormatSheetLayoutSkill(BaseSkill):
    """Column widths, row heights, auto-fit, merges and freeze panes."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "format_sheet_layout"

    @property
    def description(self) -> str:
        return (
            "Set a spreadsheet's layout so a table is actually readable: column "
            "widths, row heights, auto-fit columns to their content, merge or "
            "unmerge ranges, and freeze the top rows / left columns so headers "
            "stay visible while scrolling. Use for 'widen column A', 'fit the "
            "columns to the text', 'freeze the header row', 'merge A1:D1 for a "
            "title'. Several of these can be done in one call."
        )

    @property
    def category(self) -> str:
        return "sheets"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "sheet_id": {
                    "type": "string",
                    "description": "Sheet id or name (optional — defaults to most recent)",
                },
                "worksheet": {
                    "type": ["string", "integer"],
                    "description": "Worksheet tab name or 0-based index (optional — defaults to the first tab)",
                },
                "column_widths": {
                    "type": "array",
                    "description": "Set column widths in pixels (default 88)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "column": {"type": "string", "description": "Column letter, e.g. 'B'"},
                            "width": {"type": "number", "description": "Width in pixels"},
                        },
                        "required": ["column", "width"],
                    },
                },
                "row_heights": {
                    "type": "array",
                    "description": "Set row heights in pixels (default 24)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "row": {"type": "integer", "description": "1-based row number"},
                            "height": {"type": "number", "description": "Height in pixels"},
                        },
                        "required": ["row", "height"],
                    },
                },
                "auto_fit_columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Column letters to size to their content, e.g. ['A','B']; pass ['*'] for every used column",
                },
                "merge": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "A1 ranges to merge, e.g. ['A1:D1'] for a title banner",
                },
                "unmerge": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "A1 cells or ranges to unmerge",
                },
                "freeze_rows": {
                    "type": "integer",
                    "description": "Number of top rows to keep visible while scrolling (0 = none)",
                },
                "freeze_columns": {
                    "type": "integer",
                    "description": "Number of left columns to keep visible (0 = none)",
                },
            },
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.sheets import geometry as G
        from lazyclaw.sheets import snapshot as S
        from lazyclaw.sheets.store import save_sheet

        sid, row, err = await _load(self._config, user_id, params)
        if err:
            return err
        assert row is not None

        sheet = _worksheet(params)
        snap = row["payload"]
        done: list[str] = []

        try:
            # Merges FIRST: auto-fit skips multi-column merges, so a banner
            # merged after sizing would leave the column already blown out.
            for ref in _strings(params.get("merge")):
                r1, c1, r2, c2 = S.parse_range(ref)
                snap = G.merge_cells(snap, r1, c1, r2, c2, sheet)
                done.append(f"merged {ref.upper()}")
            for ref in _strings(params.get("unmerge")):
                r1, c1, _, _ = S.parse_range(ref)
                snap = G.unmerge_cells(snap, r1, c1, sheet)
                done.append(f"unmerged {ref.upper()}")

            # Auto-fit BEFORE explicit widths, so an explicit width for the same
            # column — the more specific instruction — wins.
            cols = _strings(params.get("auto_fit_columns"))
            if cols:
                targets = None if "*" in cols else _columns(cols)
                snap = G.auto_fit_columns(snap, targets, sheet)
                done.append(
                    "auto-fitted every column" if targets is None
                    else f"auto-fitted {', '.join(c.upper() for c in cols)}"
                )

            widths = _widths(params.get("column_widths"))
            if widths:
                snap = G.set_column_widths(snap, widths, sheet)
                done.append(f"set {len(widths)} column width(s)")

            heights = _heights(params.get("row_heights"))
            if heights:
                snap = G.set_row_heights(snap, heights, sheet)
                done.append(f"set {len(heights)} row height(s)")

            rows_frozen = params.get("freeze_rows")
            cols_frozen = params.get("freeze_columns")
            if rows_frozen is not None or cols_frozen is not None:
                current_rows, current_cols = G.frozen_counts(snap, sheet)
                snap = G.freeze_panes(
                    snap,
                    rows=current_rows if rows_frozen is None else int(rows_frozen),
                    cols=current_cols if cols_frozen is None else int(cols_frozen),
                    sheet=sheet,
                )
                new_rows, new_cols = G.frozen_counts(snap, sheet)
                done.append(
                    f"froze {new_rows} row(s) / {new_cols} column(s)"
                    if (new_rows or new_cols) else "unfroze the panes"
                )
        except (ValueError, KeyError, IndexError, TypeError) as e:
            return f"Could not apply that layout: {e}"

        if not done:
            return (
                "Nothing to change. Pass column_widths, row_heights, "
                "auto_fit_columns, merge, unmerge, freeze_rows or freeze_columns."
            )

        await save_sheet(self._config, user_id, row["name"], snap, sheet_id=sid)
        return f"Layout updated in **{row['name']}**: " + "; ".join(done) + "."


# ───────────────────────── parameter coercion ───────────────────────
#
# Everything below takes UNTRUSTED shapes (an LLM filled the schema). A bad
# entry is skipped, never raised, and never sinks its good siblings.

def _strings(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out = [str(v).strip() for v in raw[:MAX_LAYOUT_OPS] if isinstance(v, (str, int))]
    return [v for v in out if v]


def _columns(letters: list[str]) -> list[int]:
    from lazyclaw.sheets import snapshot as S

    out: list[int] = []
    for letter in letters:
        try:
            out.append(S.letter_to_col(letter))
        except ValueError:
            continue
    return out


def _widths(raw: Any) -> dict[int, float]:
    from lazyclaw.sheets import snapshot as S

    out: dict[int, float] = {}
    if not isinstance(raw, list):
        return out
    for entry in raw[:MAX_LAYOUT_OPS]:
        if not isinstance(entry, dict):
            continue
        column, width = entry.get("column"), entry.get("width")
        if column is None or not isinstance(width, (int, float)):
            continue
        try:
            index = (
                int(column) if str(column).strip().isdigit()
                else S.letter_to_col(str(column))
            )
        except (ValueError, TypeError):
            continue
        out[index] = float(width)
    return out


def _heights(raw: Any) -> dict[int, float]:
    out: dict[int, float] = {}
    if not isinstance(raw, list):
        return out
    for entry in raw[:MAX_LAYOUT_OPS]:
        if not isinstance(entry, dict):
            continue
        row, height = entry.get("row"), entry.get("height")
        if not isinstance(height, (int, float)):
            continue
        try:
            # 1-based on the wire (what a user says), 0-based in the snapshot.
            index = int(row) - 1
        except (TypeError, ValueError):
            continue
        if index < 0:
            continue
        out[index] = float(height)
    return out
