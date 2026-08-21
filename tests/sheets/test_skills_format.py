"""The two agent-facing formatting skills.

`format_cells` is the styling verb, `format_sheet_layout` the geometry one.
They split on the natural seam — style params and geometry params share nothing,
and a single 20-property schema is exactly where a model fills the wrong half.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.sheets import geometry as G
from lazyclaw.sheets import snapshot as S
from lazyclaw.sheets import styles as ST
from lazyclaw.sheets.store import get_sheet, save_sheet
from lazyclaw.skills.builtin.sheets import CreateSheetSkill, SetCellsSkill
from lazyclaw.skills.builtin.sheets_format import (
    FormatCellsSkill,
    FormatSheetLayoutSkill,
)

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


@pytest.fixture
async def budget(cfg):
    """A saved sheet holding a small budget table. Returns its id."""
    await CreateSheetSkill(config=cfg).execute("u1", {"name": "Budget"})
    await SetCellsSkill(config=cfg).execute("u1", {
        "sheet_id": "Budget",
        "cells": [
            {"cell": "A1", "value": "Item"}, {"cell": "B1", "value": "Cost"},
            {"cell": "A2", "value": "Rent"}, {"cell": "B2", "value": 1200},
            {"cell": "A3", "value": "Food"}, {"cell": "B3", "value": 430.5},
            {"cell": "B4", "formula": "=SUM(B2:B3)"},
        ],
    })
    from lazyclaw.sheets.store import list_sheets
    rows = await list_sheets(cfg, "u1")
    return rows[0]["id"]


async def _payload(cfg, sheet_id):
    return (await get_sheet(cfg, "u1", sheet_id))["payload"]


# ───────────────────────── format_cells ─────────────────────────────

async def test_format_cells_bolds_a_header_row(cfg, budget):
    out = await FormatCellsSkill(config=cfg).execute(
        "u1", {"sheet_id": budget, "range": "A1:B1", "bold": True}
    )
    assert "A1:B1" in out
    snap = await _payload(cfg, budget)
    for col in (0, 1):
        assert ST.get_style_view(snap, 0, col) == {"bold": True}


async def test_format_cells_applies_a_number_format(cfg, budget):
    await FormatCellsSkill(config=cfg).execute(
        "u1", {"sheet_id": budget, "range": "B2:B4", "number_format": "currency"}
    )
    snap = await _payload(cfg, budget)
    assert ST.resolve_style(snap, 1, 1)["n"]["pattern"] == "$#,##0.00"


async def test_format_cells_preserves_values_and_formulas(cfg, budget):
    await FormatCellsSkill(config=cfg).execute(
        "u1", {"sheet_id": budget, "range": "A1:B4", "bold": True}
    )
    snap = await _payload(cfg, budget)
    assert S.get_cell(snap, 1, 1)["v"] == 1200
    assert S.get_cell(snap, 3, 1)["f"] == "=SUM(B2:B3)"


async def test_format_cells_accumulates_across_calls(cfg, budget):
    skill = FormatCellsSkill(config=cfg)
    await skill.execute("u1", {"sheet_id": budget, "range": "A1", "bold": True})
    await skill.execute("u1", {"sheet_id": budget, "range": "A1", "bg": "yellow"})
    view = ST.get_style_view(await _payload(cfg, budget), 0, 0)
    assert view == {"bold": True, "bg": "#FFFF00"}


async def test_format_cells_clear_strips_formatting(cfg, budget):
    skill = FormatCellsSkill(config=cfg)
    await skill.execute("u1", {"sheet_id": budget, "range": "A1", "bold": True})
    await skill.execute("u1", {"sheet_id": budget, "range": "A1", "clear": True})
    snap = await _payload(cfg, budget)
    assert ST.get_style_view(snap, 0, 0) == {}
    assert S.get_cell(snap, 0, 0)["v"] == "Item"


async def test_format_cells_requires_a_range(cfg, budget):
    out = await FormatCellsSkill(config=cfg).execute("u1", {"sheet_id": budget})
    assert "range" in out.lower()


async def test_format_cells_rejects_a_bad_range(cfg, budget):
    out = await FormatCellsSkill(config=cfg).execute(
        "u1", {"sheet_id": budget, "range": "not-a-range", "bold": True}
    )
    assert "range" in out.lower()


async def test_format_cells_with_no_recognised_field_says_so(cfg, budget):
    out = await FormatCellsSkill(config=cfg).execute(
        "u1", {"sheet_id": budget, "range": "A1", "sparkle": True}
    )
    assert "formatting" in out.lower()


# ───────────────────────── format_sheet_layout ──────────────────────

async def test_layout_sets_column_widths(cfg, budget):
    out = await FormatSheetLayoutSkill(config=cfg).execute(
        "u1",
        {"sheet_id": budget, "column_widths": [{"column": "A", "width": 160}]},
    )
    assert "width" in out.lower()
    assert G.get_column_width(await _payload(cfg, budget), 0) == 160


async def test_layout_sets_row_heights_from_1_based_rows(cfg, budget):
    await FormatSheetLayoutSkill(config=cfg).execute(
        "u1", {"sheet_id": budget, "row_heights": [{"row": 1, "height": 32}]}
    )
    snap = await _payload(cfg, budget)
    assert G.get_row_height(snap, 0) == 32, "row 1 must mean index 0"


async def test_layout_auto_fits_named_columns(cfg, budget):
    await FormatSheetLayoutSkill(config=cfg).execute(
        "u1", {"sheet_id": budget, "auto_fit_columns": ["A"]}
    )
    snap = await _payload(cfg, budget)
    assert G.get_column_width(snap, 0) == G.measure_column_width(snap, 0)


async def test_layout_auto_fits_everything_with_a_star(cfg, budget):
    await FormatSheetLayoutSkill(config=cfg).execute(
        "u1", {"sheet_id": budget, "auto_fit_columns": ["*"]}
    )
    snap = await _payload(cfg, budget)
    assert set(snap["sheets"][snap["sheetOrder"][0]]["columnData"]) == {"0", "1"}


async def test_layout_freezes_and_unfreezes(cfg, budget):
    skill = FormatSheetLayoutSkill(config=cfg)
    await skill.execute("u1", {"sheet_id": budget, "freeze_rows": 1})
    assert G.frozen_counts(await _payload(cfg, budget)) == (1, 0)
    await skill.execute("u1", {"sheet_id": budget, "freeze_rows": 0})
    assert G.frozen_counts(await _payload(cfg, budget)) == (0, 0)


async def test_layout_merges_and_unmerges(cfg, budget):
    skill = FormatSheetLayoutSkill(config=cfg)
    await skill.execute("u1", {"sheet_id": budget, "merge": ["A1:B1"]})
    assert G.merged_range_at(await _payload(cfg, budget), 0, 1) is not None
    await skill.execute("u1", {"sheet_id": budget, "unmerge": ["A1"]})
    assert G.merged_range_at(await _payload(cfg, budget), 0, 1) is None


async def test_layout_applies_merge_before_autofit(cfg, budget):
    """Order matters: a merged banner must not blow out column A."""
    snap = S.set_cell(
        await _payload(cfg, budget), 0, 0,
        value="An extremely long banner title that would blow the column out",
    )
    await save_sheet(cfg, "u1", "Budget", snap, sheet_id=budget)
    await FormatSheetLayoutSkill(config=cfg).execute(
        "u1",
        {"sheet_id": budget, "merge": ["A1:B1"], "auto_fit_columns": ["A"]},
    )
    assert G.get_column_width(await _payload(cfg, budget), 0) < G.AUTOFIT_MAX_PX


async def test_layout_explicit_width_beats_autofit(cfg, budget):
    """The more specific instruction wins when both target one column."""
    await FormatSheetLayoutSkill(config=cfg).execute(
        "u1",
        {
            "sheet_id": budget,
            "auto_fit_columns": ["A"],
            "column_widths": [{"column": "A", "width": 300}],
        },
    )
    assert G.get_column_width(await _payload(cfg, budget), 0) == 300


async def test_layout_with_nothing_to_do_says_so(cfg, budget):
    out = await FormatSheetLayoutSkill(config=cfg).execute(
        "u1", {"sheet_id": budget}
    )
    assert "nothing" in out.lower()


async def test_layout_reports_when_there_are_no_sheets(cfg):
    out = await FormatSheetLayoutSkill(config=cfg).execute("u1", {"freeze_rows": 1})
    assert "no sheets" in out.lower()


# ───────────────────────── worksheet targeting ──────────────────────

async def test_skills_target_a_named_worksheet(cfg, budget):
    """`set_cells(sheet=)` has always supported this; no skill exposed it."""
    snap = await _payload(cfg, budget)
    sid2 = "sh-second"
    snap["sheets"][sid2] = {
        "id": sid2, "name": "Second", "rowCount": 100, "columnCount": 10,
        "cellData": {"0": {"0": {"v": "x"}}},
    }
    snap["sheetOrder"].append(sid2)
    await save_sheet(cfg, "u1", "Budget", snap, sheet_id=budget)

    await FormatCellsSkill(config=cfg).execute(
        "u1",
        {"sheet_id": budget, "worksheet": "Second", "range": "A1", "bold": True},
    )
    out = await _payload(cfg, budget)
    assert ST.get_style_view(out, 0, 0, sheet="Second") == {"bold": True}
    assert ST.get_style_view(out, 0, 0, sheet=0) == {}
