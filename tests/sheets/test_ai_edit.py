"""Deterministic apply for the Sheets AI specialist (lazyclaw/sheets/ai_edit.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.sheets import ai_edit, snapshot as S
from lazyclaw.sheets.store import create_sheet

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


async def test_load_and_build_messages(cfg):
    row = await create_sheet(cfg, "u1", "Budget")
    ctx = await ai_edit.load(cfg, "u1", row["id"])
    assert ctx is not None
    msgs = ai_edit.build_messages(ctx, "add a total in C1")
    assert len(msgs) == 2
    assert "add a total in C1" in msgs[1].content


async def test_apply_values_and_formula_recalcs(cfg):
    row = await create_sheet(cfg, "u1", "Budget")
    ctx = await ai_edit.load(cfg, "u1", row["id"])
    plan = {
        "edits": [
            {"cell": "A1", "value": 10},
            {"cell": "B1", "value": 20},
            {"cell": "C1", "formula": "=A1+B1"},
        ]
    }
    res = await ai_edit.apply(cfg, "u1", row["id"], ctx, plan)
    assert res["new_id"] is None
    snap = res["snapshot"]
    c1 = S.get_cell(snap, 0, 2)
    assert S.cell_display(c1) in (30, "30", 30.0)


async def test_apply_row_col_form(cfg):
    row = await create_sheet(cfg, "u1", "S")
    ctx = await ai_edit.load(cfg, "u1", row["id"])
    res = await ai_edit.apply(
        cfg, "u1", row["id"], ctx, {"edits": [{"row": 1, "col": 0, "value": "Total"}]}
    )
    assert S.cell_display(S.get_cell(res["snapshot"], 1, 0)) == "Total"


async def test_apply_rejects_empty_edits(cfg):
    row = await create_sheet(cfg, "u1", "S")
    ctx = await ai_edit.load(cfg, "u1", row["id"])
    with pytest.raises(ValueError):
        await ai_edit.apply(cfg, "u1", row["id"], ctx, {"edits": []})


# ───────────── formats + layout in the same plan (2026-08-21) ────────


async def test_apply_values_then_formats_then_layout(cfg):
    """One plan carrying all three sections must compose end to end.

    The load-bearing assertion is the last one: `recalc` runs AFTER the styles
    are written, and it must fill the formula's `v` without clobbering the
    cell's `s`.
    """
    row = await create_sheet(cfg, "u1", "Budget")
    ctx = await ai_edit.load(cfg, "u1", row["id"])
    out = await ai_edit.apply(cfg, "u1", row["id"], ctx, {
        "edits": [
            {"cell": "A1", "value": "Item"}, {"cell": "B1", "value": "Cost"},
            {"cell": "A2", "value": "Rent"}, {"cell": "B2", "value": 1200},
            {"cell": "A3", "value": "Food"}, {"cell": "B3", "value": 430.5},
            {"cell": "A4", "value": "TOTAL"},
            {"cell": "B4", "formula": "=SUM(B2:B3)"},
        ],
        "formats": [
            {"range": "A1:B1", "bold": True, "bg": "#E8E8E8"},
            {"range": "B2:B4", "number_format": "currency"},
        ],
        "layout": {
            "auto_fit_columns": ["*"],
            "freeze_rows": 1,
            "column_widths": [{"column": "A", "width": 160}],
        },
    })

    from lazyclaw.sheets import geometry as G
    from lazyclaw.sheets import styles as ST

    snap = out["snapshot"]

    # Values landed, and the formula recalculated.
    assert S.cell_display(S.get_cell(snap, 1, 1)) == 1200
    assert S.get_cell(snap, 3, 1)["v"] == pytest.approx(1630.5)

    # Styles landed.
    assert ST.get_style_view(snap, 0, 0) == {"bold": True, "bg": "#E8E8E8"}
    assert ST.resolve_style(snap, 3, 1)["n"]["pattern"] == "$#,##0.00"

    # Geometry landed — explicit width beat the auto-fit for column A.
    assert G.get_column_width(snap, 0) == 160
    assert G.get_column_width(snap, 1) != G.DEFAULT_COL_WIDTH
    assert G.frozen_counts(snap) == (1, 0)

    # THE regression this guards: recalc must not strip the style off B4.
    assert "s" in S.get_cell(snap, 3, 1)
    assert ST.resolve_style(snap, 3, 1)["n"]["pattern"] == "$#,##0.00"

    assert "cell" in out["summary"] and "format" in out["summary"]


async def test_apply_accepts_a_formatting_only_plan(cfg):
    """No 'edits' at all — 'bold the header' is a complete instruction."""
    row = await create_sheet(cfg, "u1", "Budget")
    ctx = await ai_edit.load(cfg, "u1", row["id"])
    ctx["payload"] = S.set_cells(ctx["payload"], [{"cell": "A1", "value": "Item"}])

    out = await ai_edit.apply(cfg, "u1", row["id"], ctx, {
        "formats": [{"range": "A1", "bold": True}],
    })

    from lazyclaw.sheets import styles as ST
    assert ST.get_style_view(out["snapshot"], 0, 0) == {"bold": True}


async def test_formatting_only_plan_is_not_judged_empty(cfg):
    """Paired with the apply guard — otherwise the specialist burns a retry
    and then fails outright on a perfectly good plan."""
    assert ai_edit.is_empty_plan({"formats": [{"range": "A1", "bold": True}]}) is False
    assert ai_edit.is_empty_plan({"layout": {"freeze_rows": 1}}) is False
    assert ai_edit.is_empty_plan({"edits": [], "formats": [], "layout": {}}) is True
    assert ai_edit.is_empty_plan({"formats": [{"bold": True}]}) is True


async def test_legacy_edits_only_plans_still_work(cfg):
    """Every plan written against the old shape must validate unchanged."""
    row = await create_sheet(cfg, "u1", "Budget")
    ctx = await ai_edit.load(cfg, "u1", row["id"])
    out = await ai_edit.apply(cfg, "u1", row["id"], ctx, {
        "edits": [{"cell": "A1", "value": 10}],
    })
    assert S.cell_display(S.get_cell(out["snapshot"], 0, 0)) == 10


async def test_apply_rejects_a_plan_with_nothing_in_it(cfg):
    row = await create_sheet(cfg, "u1", "Budget")
    ctx = await ai_edit.load(cfg, "u1", row["id"])
    with pytest.raises(ValueError, match="edits.*formats.*layout"):
        await ai_edit.apply(cfg, "u1", row["id"], ctx, {"edits": []})
