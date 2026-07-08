"""Agent skills for private encrypted spreadsheets.

NL control over :mod:`lazyclaw.sheets.store`. The agent creates sheets, reads
the value grid, writes cells/formulas, and recalculates — all over the same
encrypted Univer snapshot the web editor uses. Channel-agnostic: identical
behaviour in Telegram, Web UI chat, and CLI. Export + delivery live in the
``send_sheet`` / ``export_sheet`` skills (added with the channel attachment
support).

Cells are addressed either in A1 form (``{"cell": "A3", ...}``) or by 0-based
``row``/``col`` ints. Edits are recalculated server-side (best effort) so the
grid and any export carry computed values even when no browser is open.
"""

from __future__ import annotations

import logging
from typing import Any

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

_MAX_PREVIEW_ROWS = 100
_MAX_PREVIEW_COLS = 26


async def _resolve_sheet_id(
    config: Any, user_id: str, ref: str | None
) -> tuple[str | None, str | None]:
    """Resolve a sheet reference (id, exact name, or substring) to an id.

    Returns ``(sheet_id, None)`` on success or ``(None, error_message)``.
    With no ref, picks the most recently updated sheet.
    """
    from lazyclaw.sheets.store import list_sheets

    rows = await list_sheets(config, user_id)
    if not rows:
        return None, "You have no sheets yet — create one first."
    if not ref:
        return rows[0]["id"], None  # most recent (ordered by updated_at desc)

    ref = ref.strip()
    for r in rows:
        if r["id"] == ref:
            return r["id"], None
    low = ref.lower()
    exact = [r for r in rows if r["name"].strip().lower() == low]
    if len(exact) == 1:
        return exact[0]["id"], None
    if len(exact) > 1:
        # List the candidate ids so the caller can retry with a concrete id.
        # Without them the model is told to "use the sheet id" it doesn't have,
        # and just repeats the name-based call forever (stuck loop).
        opts = "; ".join(
            f"id={r['id']}"
            + (f" (updated {r['updated_at']})" if r.get("updated_at") else "")
            for r in exact
        )
        return None, (
            f"Multiple sheets named '{ref}'. Retry with ONE of these ids as the "
            f"sheet reference — {opts}"
        )
    subs = [r for r in rows if low in r["name"].lower()]
    if len(subs) == 1:
        return subs[0]["id"], None
    names = ", ".join(r["name"] for r in rows)
    return None, f"No sheet matching '{ref}'. You have: {names}."


def _format_grid(snap: dict[str, Any], sheet: int | str = 0) -> str:
    """Render a worksheet's value grid as a labelled text table for the LLM."""
    from lazyclaw.sheets import snapshot as S

    grid = S.as_grid(snap, sheet)
    if not grid:
        return "(empty sheet)"
    n_rows = min(len(grid), _MAX_PREVIEW_ROWS)
    n_cols = min(len(grid[0]), _MAX_PREVIEW_COLS)
    header = "    | " + " | ".join(S.col_to_letter(c) for c in range(n_cols))
    lines = [header, "    |-" + "-|-".join("-" * 3 for _ in range(n_cols))]
    for r in range(n_rows):
        cells = " | ".join(str(grid[r][c]) for c in range(n_cols))
        lines.append(f"{r + 1:>3} | {cells}")
    note = ""
    if len(grid) > n_rows or len(grid[0]) > n_cols:
        note = f"\n…(showing {n_rows}×{n_cols} of {len(grid)}×{len(grid[0])})"
    return "\n".join(lines) + note


class CreateSheetSkill(BaseSkill):
    """Create a new blank spreadsheet."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "create_sheet"

    @property
    def description(self) -> str:
        return (
            "Create a new private encrypted spreadsheet (e.g. 'make me a sheet "
            "called Budget'). Returns the sheet id to use for later edits. The "
            "user can open and edit it in the Sheets tab of the web UI."
        )

    @property
    def category(self) -> str:
        return "sheets"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Sheet name, e.g. 'Budget'"},
            },
            "required": ["name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.sheets.store import create_sheet, list_sheets

        name = (params.get("name") or "Untitled sheet").strip() or "Untitled sheet"
        # Idempotent create: reuse an existing live sheet of the same name rather
        # than spawning a silent duplicate. Duplicate names make _resolve_sheet_id
        # ambiguous and (before the id-listing fix) sent specialists into a stuck
        # loop — the 2026-07-06 "ClubBay Expenses" incident where a blank second
        # copy was created and later confused every edit.
        for s in await list_sheets(self._config, user_id):
            if s["name"].strip().lower() == name.lower():
                return (
                    f"A sheet named **{s['name']}** already exists (id `{s['id']}`) "
                    f"— reusing it instead of creating a duplicate. Add data with "
                    f"set_cells, or choose a different name to force a separate sheet."
                )
        row = await create_sheet(self._config, user_id, name)
        return f"Created sheet **{row['name']}** (id `{row['id']}`)."


class ListSheetsSkill(BaseSkill):
    """List the user's spreadsheets."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "list_sheets"

    @property
    def description(self) -> str:
        return "List all of the user's private spreadsheets (name + id + last edited)."

    @property
    def category(self) -> str:
        return "sheets"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.sheets.store import list_sheets

        rows = await list_sheets(self._config, user_id)
        if not rows:
            return "You have no sheets yet."
        lines = [f"- **{r['name']}** (id `{r['id']}`) — updated {r['updated_at']}" for r in rows]
        return f"You have {len(rows)} sheet(s):\n" + "\n".join(lines)


class ReadSheetSkill(BaseSkill):
    """Read a spreadsheet's value grid."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "read_sheet"

    @property
    def description(self) -> str:
        return (
            "Read a spreadsheet's contents as a labelled grid (column letters + "
            "row numbers) so you can reason over the data. Identify the sheet by "
            "id or name; omit to read the most recently edited one."
        )

    @property
    def category(self) -> str:
        return "sheets"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "sheet_id": {
                    "type": "string",
                    "description": "Sheet id or name (optional — defaults to most recent)",
                },
            },
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.sheets.store import get_sheet

        sid, err = await _resolve_sheet_id(self._config, user_id, params.get("sheet_id"))
        if err:
            return err
        sheet = await get_sheet(self._config, user_id, sid)
        if not sheet:
            return "Sheet not found."
        return f"Sheet **{sheet['name']}**:\n```\n{_format_grid(sheet['payload'])}\n```"


class SetCellsSkill(BaseSkill):
    """Write a batch of cells (values and/or formulas) into a sheet."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "set_cells"

    @property
    def description(self) -> str:
        return (
            "Write one or more cells into a spreadsheet, then recalculate. Each "
            "cell is {cell:'A1'} or {row:0,col:0} (0-based) with a 'value' "
            "(number/text) and/or a 'formula' like '=SUM(A1:A2)'. Use this for "
            "'put 10 in A1', 'fill column B', or adding a total formula."
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
                "cells": {
                    "type": "array",
                    "description": "Cells to write",
                    "items": {
                        "type": "object",
                        "properties": {
                            "cell": {"type": "string", "description": "A1 reference, e.g. 'B3'"},
                            "row": {"type": "integer", "description": "0-based row (if not using 'cell')"},
                            "col": {"type": "integer", "description": "0-based column (if not using 'cell')"},
                            "value": {
                                "type": ["number", "string", "boolean"],
                                "description": "Literal value",
                            },
                            "formula": {"type": "string", "description": "Formula, e.g. '=SUM(A1:A2)'"},
                        },
                    },
                },
            },
            "required": ["cells"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.sheets import snapshot as S
        from lazyclaw.sheets.recalc import recalc
        from lazyclaw.sheets.snapshot import _MD_LINK_RE
        from lazyclaw.sheets.store import get_sheet, save_sheet

        cells = params.get("cells")
        if not isinstance(cells, list) or not cells:
            return "Provide a non-empty 'cells' list."
        sid, err = await _resolve_sheet_id(self._config, user_id, params.get("sheet_id"))
        if err:
            return err
        sheet = await get_sheet(self._config, user_id, sid)
        if not sheet:
            return "Sheet not found."

        # Pre-process edits: markdown [text](url) values → real hyperlinks.
        snap = sheet["payload"]
        plain_edits = []
        for edit in cells:
            value = edit.get("value")
            formula = edit.get("formula")
            if formula is None and isinstance(value, str):
                md = _MD_LINK_RE.match(value.strip())
                if md:
                    # Resolve the cell address first so set_cell_link gets row/col.
                    if "cell" in edit:
                        row, col = S.a1_to_rc(str(edit["cell"]))
                    else:
                        row, col = int(edit["row"]), int(edit["col"])
                    snap = S.set_cell_link(
                        snap, row, col, md.group(2), display=md.group(1)
                    )
                    continue  # handled as hyperlink — skip plain write
            plain_edits.append(edit)

        try:
            if plain_edits:
                snap = S.set_cells(snap, plain_edits)
        except (ValueError, KeyError) as e:
            return f"Could not apply edits: {e}"
        snap = recalc(snap)
        await save_sheet(self._config, user_id, sheet["name"], snap, sheet_id=sid)
        return (
            f"Updated {len(cells)} cell(s) in **{sheet['name']}**.\n"
            f"```\n{_format_grid(snap)}\n```"
        )


class SetFormulaSkill(BaseSkill):
    """Set a single cell's formula (convenience over set_cells)."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "set_formula"

    @property
    def description(self) -> str:
        return (
            "Put a formula in one cell and recalculate, e.g. '=SUM(A1:A10)' in "
            "A11. Address the cell in A1 form."
        )

    @property
    def category(self) -> str:
        return "sheets"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "sheet_id": {"type": "string", "description": "Sheet id or name (optional)"},
                "cell": {"type": "string", "description": "A1 reference, e.g. 'A11'"},
                "formula": {"type": "string", "description": "Formula, e.g. '=SUM(A1:A10)'"},
            },
            "required": ["cell", "formula"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        cell = (params.get("cell") or "").strip()
        formula = (params.get("formula") or "").strip()
        if not cell or not formula:
            return "Both 'cell' and 'formula' are required."
        delegate = SetCellsSkill(config=self._config)
        return await delegate.execute(
            user_id,
            {"sheet_id": params.get("sheet_id"), "cells": [{"cell": cell, "formula": formula}]},
        )


class RecalcSheetSkill(BaseSkill):
    """Recalculate all formulas in a sheet and persist the results."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "recalc_sheet"

    @property
    def description(self) -> str:
        return "Recalculate every formula in a sheet and save the computed values."

    @property
    def category(self) -> str:
        return "sheets"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "sheet_id": {"type": "string", "description": "Sheet id or name (optional)"},
            },
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.sheets.recalc import recalc
        from lazyclaw.sheets.store import get_sheet, save_sheet

        sid, err = await _resolve_sheet_id(self._config, user_id, params.get("sheet_id"))
        if err:
            return err
        sheet = await get_sheet(self._config, user_id, sid)
        if not sheet:
            return "Sheet not found."
        updated = recalc(sheet["payload"])
        await save_sheet(self._config, user_id, sheet["name"], updated, sheet_id=sid)
        return f"Recalculated **{sheet['name']}**.\n```\n{_format_grid(updated)}\n```"


class ConvertSheetLinksSkill(BaseSkill):
    """Convert plain URLs and markdown links in a sheet into clickable hyperlinks."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "convert_sheet_links"

    @property
    def description(self) -> str:
        return (
            "Scan a spreadsheet for plain URLs (https://…) and Markdown-style "
            "[text](url) links and turn them into real clickable hyperlinks in "
            "the Univer editor. Use when cells contain bare URLs or markdown "
            "links that should be clickable. Returns a summary like "
            "'Converted 3 URLs to links in \\'Budget\\''."
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
            },
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.sheets.snapshot import convert_urls_to_links
        from lazyclaw.sheets.store import get_sheet, save_sheet

        sid, err = await _resolve_sheet_id(self._config, user_id, params.get("sheet_id"))
        if err:
            return err
        sheet = await get_sheet(self._config, user_id, sid)
        if not sheet:
            return "Sheet not found."
        snap, converted = convert_urls_to_links(sheet["payload"])
        if converted:
            try:
                await save_sheet(self._config, user_id, None, snap, sheet_id=sid)
            except LookupError:
                return "Sheet not found."
        name = sheet["name"]
        if converted == 0:
            return f"No plain URLs or markdown links found in **{name}**."
        noun = "URL" if converted == 1 else "URLs"
        return f"Converted {converted} {noun} to links in **{name}**."


class SendSheetSkill(BaseSkill):
    """Export a sheet to a file and deliver it to the user."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "send_sheet"

    @property
    def description(self) -> str:
        return (
            "Export a spreadsheet to a file and send it to the user (e.g. 'send "
            "me the budget sheet' / 'export it as csv'). Delivers as a Telegram "
            "document when Telegram is configured; always returns the web "
            "download link too. Default format is xlsx (formulas recompute on "
            "open); pass format='csv' for plain values."
        )

    @property
    def category(self) -> str:
        return "sheets"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "sheet_id": {"type": "string", "description": "Sheet id or name (optional)"},
                "format": {
                    "type": "string",
                    "enum": ["xlsx", "csv"],
                    "description": "Export format (default xlsx)",
                },
            },
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.notifications.push import push_telegram_document
        from lazyclaw.sheets.store import get_sheet
        from lazyclaw.sheets.xlsx_io import snapshot_to_csv, snapshot_to_xlsx

        fmt = (params.get("format") or "xlsx").strip().lower()
        if fmt not in ("xlsx", "csv"):
            return "Format must be 'xlsx' or 'csv'."
        sid, err = await _resolve_sheet_id(self._config, user_id, params.get("sheet_id"))
        if err:
            return err
        sheet = await get_sheet(self._config, user_id, sid)
        if not sheet:
            return "Sheet not found."

        name = sheet["name"]
        if fmt == "csv":
            content = snapshot_to_csv(sheet["payload"]).encode("utf-8")
        else:
            content = snapshot_to_xlsx(sheet["payload"])
        filename = f"{name}.{fmt}"
        download_url = f"/api/sheets/{sid}/export?format={fmt}"

        sent = await push_telegram_document(
            self._config, content, filename, caption=f"📊 {name}",
        )
        if sent:
            return f"Sent **{filename}** to your Telegram. (Web download: {download_url})"
        return (
            f"Exported **{filename}** ({len(content)} bytes). "
            f"Download it from the Sheets tab or at {download_url}."
        )
