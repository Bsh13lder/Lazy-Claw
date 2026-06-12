"""Tests for the agent sheet skills (lazyclaw/skills/builtin/sheets.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.permissions.models import ALLOW, DEFAULT_CATEGORY_PERMISSIONS
from lazyclaw.skills.builtin.sheets import (
    ConvertSheetLinksSkill,
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
        SendSheetSkill().name, ConvertSheetLinksSkill().name,
    } == {
        "create_sheet", "list_sheets", "read_sheet", "set_cells",
        "set_formula", "recalc_sheet", "send_sheet", "convert_sheet_links",
    }


# ── ConvertSheetLinksSkill ────────────────────────────────────────────────────


async def test_convert_sheet_links_happy_path(cfg):
    """convert_sheet_links finds a bare URL, converts it, returns human summary."""
    from lazyclaw.sheets.snapshot import get_sheet_links, set_cells
    from lazyclaw.sheets.store import get_sheet, save_sheet

    await CreateSheetSkill(config=cfg).execute("u1", {"name": "Links"})

    # Write a bare URL directly into the stored snapshot
    sheet_ref = await get_sheet(cfg, "u1", (await _resolve_sheet_id(cfg, "u1", "Links"))[0])
    assert sheet_ref is not None
    snap = set_cells(sheet_ref["payload"], [{"row": 0, "col": 0, "value": "https://lazyclaw.io"}])
    await save_sheet(cfg, "u1", "Links", snap, sheet_id=sheet_ref["id"])

    out = await ConvertSheetLinksSkill(config=cfg).execute("u1", {"sheet_id": "Links"})
    assert "1" in out
    assert "Links" in out
    assert "link" in out.lower()

    # Verify the link is actually persisted
    updated = await get_sheet(cfg, "u1", sheet_ref["id"])
    assert updated is not None
    links = get_sheet_links(updated["payload"])
    assert len(links) == 1
    assert links[0]["payload"] == "https://lazyclaw.io"


async def test_convert_sheet_links_no_urls_returns_message(cfg):
    """convert_sheet_links on a sheet with no URLs returns an informative message."""
    await CreateSheetSkill(config=cfg).execute("u1", {"name": "Empty"})
    await SetCellsSkill(config=cfg).execute("u1", {
        "sheet_id": "Empty",
        "cells": [{"cell": "A1", "value": "hello world"}],
    })
    out = await ConvertSheetLinksSkill(config=cfg).execute("u1", {"sheet_id": "Empty"})
    assert "No" in out or "no" in out or "0" in out


async def test_convert_sheet_links_no_sheet(cfg):
    """convert_sheet_links with no sheets returns a helpful error."""
    out = await ConvertSheetLinksSkill(config=cfg).execute("u1", {})
    assert "no sheets" in out.lower()


# ── SetCellsSkill: markdown [text](url) detection ────────────────────────────


async def test_set_cells_markdown_link_creates_hyperlink(cfg):
    """set_cells with a [text](url) value stores a real hyperlink, not plain text."""
    from lazyclaw.sheets.snapshot import get_sheet_links
    from lazyclaw.sheets.store import get_sheet

    await CreateSheetSkill(config=cfg).execute("u1", {"name": "MD"})

    out = await SetCellsSkill(config=cfg).execute("u1", {
        "sheet_id": "MD",
        "cells": [{"cell": "A1", "value": "[Invoice](https://inv.example.com/1)"}],
    })
    assert "Updated 1 cell" in out

    sheet = await get_sheet(cfg, "u1", (await _resolve_sheet_id(cfg, "u1", "MD"))[0])
    assert sheet is not None
    links = get_sheet_links(sheet["payload"])
    assert len(links) == 1
    assert links[0]["payload"] == "https://inv.example.com/1"

    # Display text is 'Invoice', not the raw markdown
    from lazyclaw.sheets.snapshot import get_cell
    cell = get_cell(sheet["payload"], 0, 0)
    assert cell is not None
    assert cell.get("v") == "Invoice"


async def test_set_cells_markdown_and_plain_in_same_batch(cfg):
    """Mixed batch: markdown cell becomes a link, plain value stays plain text."""
    from lazyclaw.sheets.snapshot import get_sheet_links
    from lazyclaw.sheets.store import get_sheet

    await CreateSheetSkill(config=cfg).execute("u1", {"name": "Mixed"})

    out = await SetCellsSkill(config=cfg).execute("u1", {
        "sheet_id": "Mixed",
        "cells": [
            {"cell": "A1", "value": "[Link](https://example.com)"},
            {"cell": "B1", "value": "plain text"},
            {"cell": "C1", "value": 42},
        ],
    })
    assert "Updated 3 cell" in out

    sid = (await _resolve_sheet_id(cfg, "u1", "Mixed"))[0]
    sheet = await get_sheet(cfg, "u1", sid)
    assert sheet is not None
    snap = sheet["payload"]

    links = get_sheet_links(snap)
    assert len(links) == 1
    assert links[0]["payload"] == "https://example.com"

    from lazyclaw.sheets.snapshot import get_cell
    plain = get_cell(snap, 0, 1)  # B1
    assert plain is not None
    assert plain.get("v") == "plain text"


async def test_set_cells_non_markdown_url_in_mixed_text_stays_plain(cfg):
    """A URL embedded in surrounding text is NOT turned into a hyperlink by set_cells."""
    from lazyclaw.sheets.snapshot import get_sheet_links
    from lazyclaw.sheets.store import get_sheet

    await CreateSheetSkill(config=cfg).execute("u1", {"name": "NM"})

    await SetCellsSkill(config=cfg).execute("u1", {
        "sheet_id": "NM",
        "cells": [{"cell": "A1", "value": "see https://example.com today"}],
    })

    sid = (await _resolve_sheet_id(cfg, "u1", "NM"))[0]
    sheet = await get_sheet(cfg, "u1", sid)
    assert sheet is not None
    links = get_sheet_links(sheet["payload"])
    assert links == []
