"""Tests for the agent sheet skills (lazyclaw/skills/builtin/sheets.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.permissions.models import ALLOW, DEFAULT_CATEGORY_PERMISSIONS
from lazyclaw.skills.builtin.sheets import (
    CreateSheetSkill,
    ListSheetsSkill,
    ReadSheetSkill,
    RecalcSheetSkill,
    SendSheetSkill,
    SetCellsSkill,
    SetFormulaSkill,
    _resolve_sheet_id,
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


async def test_create_then_list(cfg):
    out = await CreateSheetSkill(config=cfg).execute("u1", {"name": "Budget"})
    assert "Budget" in out
    listed = await ListSheetsSkill(config=cfg).execute("u1", {})
    assert "Budget" in listed


async def test_set_cells_writes_and_recalcs(cfg):
    await CreateSheetSkill(config=cfg).execute("u1", {"name": "Budget"})
    out = await SetCellsSkill(config=cfg).execute("u1", {
        "sheet_id": "Budget",
        "cells": [
            {"cell": "A1", "value": 10},
            {"cell": "A2", "value": 20},
            {"cell": "A3", "formula": "=SUM(A1:A2)"},
        ],
    })
    assert "Updated 3 cell" in out
    assert "30" in out  # recalc filled the SUM

    grid = await ReadSheetSkill(config=cfg).execute("u1", {"sheet_id": "Budget"})
    assert "30" in grid


async def test_set_formula_single_cell(cfg):
    await CreateSheetSkill(config=cfg).execute("u1", {"name": "S"})
    await SetCellsSkill(config=cfg).execute("u1", {
        "sheet_id": "S",
        "cells": [{"cell": "A1", "value": 6}, {"cell": "B1", "value": 7}],
    })
    out = await SetFormulaSkill(config=cfg).execute("u1", {
        "sheet_id": "S", "cell": "C1", "formula": "=A1*B1",
    })
    assert "42" in out


async def test_recalc_sheet_skill(cfg):
    await CreateSheetSkill(config=cfg).execute("u1", {"name": "R"})
    await SetCellsSkill(config=cfg).execute("u1", {
        "sheet_id": "R", "cells": [{"cell": "A1", "value": 5}, {"cell": "A2", "formula": "=A1*2"}],
    })
    out = await RecalcSheetSkill(config=cfg).execute("u1", {"sheet_id": "R"})
    assert "10" in out


async def test_read_sheet_no_sheets(cfg):
    out = await ReadSheetSkill(config=cfg).execute("u1", {})
    assert "no sheets" in out.lower()


async def test_set_cells_requires_cells(cfg):
    await CreateSheetSkill(config=cfg).execute("u1", {"name": "X"})
    out = await SetCellsSkill(config=cfg).execute("u1", {"sheet_id": "X", "cells": []})
    assert "cells" in out.lower()


async def test_resolve_by_id_name_substring_and_missing(cfg):
    created = await CreateSheetSkill(config=cfg).execute("u1", {"name": "Quarterly Report"})
    # id appears in the create message inside backticks
    sid = created.split("`")[1]

    by_id, err = await _resolve_sheet_id(cfg, "u1", sid)
    assert by_id == sid and err is None

    by_name, err = await _resolve_sheet_id(cfg, "u1", "quarterly report")
    assert by_name == sid and err is None

    by_sub, err = await _resolve_sheet_id(cfg, "u1", "quarter")
    assert by_sub == sid and err is None

    missing, err = await _resolve_sheet_id(cfg, "u1", "nonexistent")
    assert missing is None and err is not None


async def test_send_sheet_delivers_via_telegram(cfg, monkeypatch):
    await CreateSheetSkill(config=cfg).execute("u1", {"name": "Budget"})
    await SetCellsSkill(config=cfg).execute("u1", {
        "sheet_id": "Budget", "cells": [{"cell": "A1", "value": 5}],
    })

    import lazyclaw.notifications.push as push_mod
    captured: dict = {}

    async def fake_push(config, content, filename, *, caption=None):
        captured["filename"] = filename
        captured["bytes"] = len(content)
        captured["caption"] = caption
        return True

    monkeypatch.setattr(push_mod, "push_telegram_document", fake_push)
    out = await SendSheetSkill(config=cfg).execute("u1", {"sheet_id": "Budget"})
    assert captured["filename"] == "Budget.xlsx"
    assert captured["bytes"] > 0
    assert "Sent" in out and "Budget.xlsx" in out


async def test_send_sheet_fallback_when_not_configured(cfg, monkeypatch):
    await CreateSheetSkill(config=cfg).execute("u1", {"name": "Report"})

    import lazyclaw.notifications.push as push_mod

    async def fake_push(config, content, filename, *, caption=None):
        return False  # Telegram not configured

    monkeypatch.setattr(push_mod, "push_telegram_document", fake_push)
    out = await SendSheetSkill(config=cfg).execute("u1", {"sheet_id": "Report", "format": "csv"})
    assert "Report.csv" in out
    assert "/api/sheets/" in out and "format=csv" in out


async def test_push_telegram_document_skips_without_config(cfg, monkeypatch):
    from lazyclaw.notifications.push import push_telegram_document

    monkeypatch.delenv("TELEGRAM_ADMIN_CHAT", raising=False)
    # cfg has no telegram_bot_token → best-effort returns False, never raises
    assert await push_telegram_document(cfg, b"data", "x.xlsx") is False


async def test_permission_default_is_allow():
    assert DEFAULT_CATEGORY_PERMISSIONS.get("sheets") == ALLOW


async def test_skill_metadata():
    assert CreateSheetSkill().category == "sheets"
    assert ListSheetsSkill().read_only is True
    assert ReadSheetSkill().read_only is True
    assert SetCellsSkill().read_only is False
    # names are stable tool identifiers
    assert {
        CreateSheetSkill().name, ListSheetsSkill().name, ReadSheetSkill().name,
        SetCellsSkill().name, SetFormulaSkill().name, RecalcSheetSkill().name,
        SendSheetSkill().name,
    } == {
        "create_sheet", "list_sheets", "read_sheet", "set_cells",
        "set_formula", "recalc_sheet", "send_sheet",
    }
