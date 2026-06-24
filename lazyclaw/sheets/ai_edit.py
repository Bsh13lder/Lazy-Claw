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
from lazyclaw.sheets import snapshot as S
from lazyclaw.sheets.recalc import recalc
from lazyclaw.sheets.store import get_sheet, save_sheet

PLAN_SHAPE = (
    '{"edits": [ {"cell": "A1", "value": 10}, '
    '{"cell": "C1", "formula": "=SUM(A1:B1)"} ]}'
)

_SYSTEM = (
    "You edit ONE spreadsheet. Read the CURRENT GRID and the INSTRUCTION, then "
    "reply with ONLY a JSON object — no prose, no code fence — of this shape:\n"
    f"{PLAN_SHAPE}\n"
    "Rules:\n"
    "- Each edit targets a cell by A1 ref ('cell') or by 0-based 'row'+'col'.\n"
    "- Set a literal with 'value' or an Excel formula (leading '=') with "
    "'formula'. The user does not know formulas — translate their request "
    "('add a total', 'average column B') into the right formula yourself.\n"
    "- Only include the cells you actually change.\n"
    "- Do not invent data; compute from the grid that is given."
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


def _normalize_edits(raw: Any) -> list[dict[str, Any]]:
    edits: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return edits
    for e in raw:
        if not isinstance(e, dict):
            continue
        edit: dict[str, Any] = {}
        if "cell" in e:
            edit["cell"] = str(e["cell"])
        elif "row" in e and "col" in e:
            try:
                edit["row"] = int(e["row"])
                edit["col"] = int(e["col"])
            except (TypeError, ValueError):
                continue
        else:
            continue
        if e.get("formula") not in (None, ""):
            edit["formula"] = str(e["formula"])
        elif "value" in e:
            edit["value"] = e["value"]
        edits.append(edit)
    return edits


def is_empty_plan(plan: dict[str, Any]) -> bool:
    """True if the plan normalises to zero cell edits (a no-op).

    Catches ``{"edits": []}`` and plans whose edits all fail validation, so
    the specialist retries with a corrective hint rather than letting
    :func:`apply` raise.
    """
    if not isinstance(plan, dict):
        return True
    return not _normalize_edits(plan.get("edits"))


async def apply(
    config: Any, user_id: str, doc_id: str, ctx: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    edits = _normalize_edits(plan.get("edits"))
    if not edits:
        raise ValueError("plan needs a non-empty 'edits' list")
    updated = S.set_cells(ctx["payload"], edits)
    updated = recalc(updated)
    await save_sheet(config, user_id, ctx["name"], updated, sheet_id=doc_id)
    fresh = await get_sheet(config, user_id, doc_id)
    return {
        "summary": f"Updated {len(edits)} cell(s).",
        "snapshot": fresh["payload"] if fresh else updated,
        "new_id": None,
    }
