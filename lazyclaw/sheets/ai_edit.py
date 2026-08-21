"""Sheets strategy for the in-editor AI specialist.

LLM returns a strict-JSON edit plan; we apply it with the same pure helpers the
``set_cells`` skill uses (set → recalc → save). Plan shape:

    {"edits": [
        {"cell": "A1", "value": 10},
        {"cell": "C1", "formula": "=SUM(A1:B1)"},
        {"row": 2, "col": 0, "value": "Total"}
    ]}
"""

from __future__ import annotations

from typing import Any

from lazyclaw.llm.providers.base import LLMMessage
from lazyclaw.sheets import ai_edit_plan as P
from lazyclaw.sheets import geometry as G
from lazyclaw.sheets import snapshot as S
from lazyclaw.sheets import styles as ST
from lazyclaw.sheets.recalc import recalc
from lazyclaw.sheets.store import get_sheet, save_sheet

PLAN_SHAPE = (
    '{"edits": [ {"cell": "A1", "value": 10}, '
    '{"cell": "C1", "formula": "=SUM(A1:B1)"} ], '
    '"formats": [ {"range": "A1:C1", "bold": true, "bg": "#e8e8e8"}, '
    '{"range": "B2:B9", "number_format": "currency"} ], '
    '"layout": {"column_widths": [{"column": "A", "width": 160}], '
    '"auto_fit_columns": ["B", "C"], "merge": ["A1:C1"], "freeze_rows": 1}}'
)

_SYSTEM = (
    "You edit ONE spreadsheet. Read the CURRENT GRID and the INSTRUCTION, then "
    "reply with ONLY a JSON object — no prose, no code fence — of this shape:\n"
    f"{PLAN_SHAPE}\n"
    "Rules:\n"
    "- 'edits', 'formats' and 'layout' are ALL OPTIONAL — include only the "
    "ones the instruction needs. A pure formatting request has no 'edits'.\n"
    "- Each edit targets a cell by A1 ref ('cell') or by 0-based 'row'+'col'.\n"
    "- Set a literal with 'value' or an Excel formula (leading '=') with "
    "'formula'. The user does not know formulas — translate their request "
    "('add a total', 'average column B') into the right formula yourself.\n"
    "- Only include the cells you actually change.\n"
    "- Do not invent data; compute from the grid that is given.\n"
    "- 'formats' entries take an A1 'range' plus any of: bold, italic, "
    "underline, strike, color, bg (either '#RRGGBB' or a colour name), align "
    "(left|center|right), valign (top|middle|bottom), wrap, number_format "
    "('currency'|'percent'|'integer'|'decimal'|'date' or an Excel pattern like "
    "'#,##0.00'), font_size, font. Omit a field to leave it as it is; pass "
    "false to turn a flag off; pass clear:true to strip all formatting.\n"
    "- 'layout' takes column_widths [{column:'A', width:160}] in pixels, "
    "row_heights [{row:1, height:32}] with a 1-BASED row, auto_fit_columns "
    "['A','B'] (or ['*'] for all), merge ['A1:C1'], unmerge ['A1'], "
    "freeze_rows, freeze_columns.\n"
    "- Ranges are always A1 and 1-based; only 'edits' may use 0-based "
    "row/col.\n"
    "- When you have just built a table, make it readable without being asked: "
    "bold the header row, freeze_rows 1, auto_fit_columns ['*'], and give money "
    "columns number_format 'currency'."
)

_MAX_ROWS = 50
_MAX_COLS = 26


async def load(config: Any, user_id: str, doc_id: str) -> dict[str, Any] | None:
    return await get_sheet(config, user_id, doc_id)


def _render_grid(snap: dict[str, Any]) -> str:
    rows, cols = S.used_bounds(snap)
    rows = min(rows, _MAX_ROWS)
    cols = min(cols, _MAX_COLS)
    if rows == 0 or cols == 0:
        return "(empty sheet)"
    grid = S.as_grid(snap)
    lines = []
    header = "   " + " | ".join(S.col_to_letter(c) for c in range(cols))
    lines.append(header)
    for r in range(rows):
        row = grid[r] if r < len(grid) else []
        cells = []
        for c in range(cols):
            val = row[c] if c < len(row) else ""
            cells.append("" if val is None else str(val))
        lines.append(f"{r + 1:>2} " + " | ".join(cells))
    return "\n".join(lines)


def build_messages(ctx: dict[str, Any], instruction: str) -> list[LLMMessage]:
    user = (
        f"CURRENT GRID (row numbers are 1-based for display; use 0-based "
        f"row/col or A1 in edits):\n{_render_grid(ctx['payload'])}\n\n"
        f"INSTRUCTION:\n{instruction}"
    )
    return [
        LLMMessage(role="system", content=_SYSTEM),
        LLMMessage(role="user", content=user),
    ]


#: Kept as a module-level alias — the normalisers moved to ``ai_edit_plan`` so
#: they can be tested without this module's async DB fixture.
_normalize_edits = P.normalize_edits


def is_empty_plan(plan: dict[str, Any]) -> bool:
    """True if the plan normalises to no work at all.

    Catches ``{"edits": []}`` and plans whose entries all fail validation, so
    the specialist retries with a corrective hint rather than letting
    :func:`apply` raise.

    MUST consider all three sections: a pure-formatting plan
    (``{"formats": [...]}`` with no edits) is perfectly valid work, and judging
    it empty would burn a retry and then fail the edit outright. This and
    :func:`apply`'s guard below are a paired change — either alone leaves a
    broken path.
    """
    if not isinstance(plan, dict):
        return True
    return not (
        P.normalize_edits(plan.get("edits"))
        or P.normalize_formats(plan.get("formats"))
        or P.normalize_layout(plan.get("layout"))
    )


def _apply_formats(
    snap: dict[str, Any], formats: list[dict[str, Any]]
) -> dict[str, Any]:
    for op in formats:
        r1, c1, r2, c2 = S.parse_range(op["range"])
        if op.get("clear"):
            snap = ST.clear_style(snap, r1, c1, r2, c2)
        else:
            friendly = {k: v for k, v in op.items() if k != "range"}
            snap = ST.apply_style(snap, r1, c1, r2, c2, friendly)
    return snap


def _apply_layout(snap: dict[str, Any], layout: dict[str, Any]) -> dict[str, Any]:
    """Apply geometry in the one order that composes correctly.

    Merges first, because auto-fit skips multi-column merges — sizing before
    merging would leave a column already blown out by a banner. Auto-fit next,
    then EXPLICIT widths, so a width the user named beats a computed fit.
    """
    for ref in layout.get("merge", []):
        r1, c1, r2, c2 = S.parse_range(ref)
        snap = G.merge_cells(snap, r1, c1, r2, c2)
    for ref in layout.get("unmerge", []):
        r1, c1, _, _ = S.parse_range(ref)
        snap = G.unmerge_cells(snap, r1, c1)

    autofit = layout.get("auto_fit_columns")
    if autofit is not None:
        cols = None if autofit == P.AUTOFIT_ALL else autofit
        snap = G.auto_fit_columns(snap, cols)

    if layout.get("column_widths"):
        snap = G.set_column_widths(snap, layout["column_widths"])
    if layout.get("row_heights"):
        snap = G.set_row_heights(snap, layout["row_heights"])

    rows, cols_frozen = layout.get("freeze_rows"), layout.get("freeze_columns")
    if rows is not None or cols_frozen is not None:
        current_rows, current_cols = G.frozen_counts(snap)
        snap = G.freeze_panes(
            snap,
            rows=current_rows if rows is None else rows,
            cols=current_cols if cols_frozen is None else cols_frozen,
        )
    return snap


def _summary(n_edits: int, n_formats: int, layout: dict[str, Any]) -> str:
    parts = []
    if n_edits:
        parts.append(f"Updated {n_edits} cell(s)")
    if n_formats:
        parts.append(f"formatted {n_formats} range(s)")
    if layout:
        parts.append("adjusted layout")
    return ", ".join(parts).capitalize() + "." if parts else "No changes."


async def apply(
    config: Any, user_id: str, doc_id: str, ctx: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    edits = P.normalize_edits(plan.get("edits"))
    formats = P.normalize_formats(plan.get("formats"))
    layout = P.normalize_layout(plan.get("layout"))
    if not (edits or formats or layout):
        raise ValueError(
            "plan needs at least one of 'edits', 'formats' or 'layout'"
        )

    # Values first: auto-fit measures final content, and a format applied to a
    # not-yet-existing cell then lands on a cell that exists. Recalc last —
    # it only writes `v` into existing cells, so it can't clobber a style.
    updated = S.set_cells(ctx["payload"], edits) if edits else ctx["payload"]
    if formats:
        updated = _apply_formats(updated, formats)
    if layout:
        updated = _apply_layout(updated, layout)
    updated = recalc(updated)

    await save_sheet(config, user_id, ctx["name"], updated, sheet_id=doc_id)
    fresh = await get_sheet(config, user_id, doc_id)
    return {
        "summary": _summary(len(edits), len(formats), layout),
        "snapshot": fresh["payload"] if fresh else updated,
        "new_id": None,
    }
