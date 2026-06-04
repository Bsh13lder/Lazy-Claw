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
