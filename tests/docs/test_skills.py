"""Tests for the agent doc skills (lazyclaw/skills/builtin/docs.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.permissions.models import ALLOW, DEFAULT_CATEGORY_PERMISSIONS
from lazyclaw.skills.builtin.docs import (
    AppendToDocSkill,
    CreateDocSkill,
    ListDocsSkill,
    ReadDocSkill,
    SendDocSkill,
    SetDocContentSkill,
    _resolve_doc_id,
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
    out = await CreateDocSkill(config=cfg).execute("u1", {"name": "Letter"})
    assert "Letter" in out
    listed = await ListDocsSkill(config=cfg).execute("u1", {})
    assert "Letter" in listed


async def test_create_read_append_round_trip(cfg):
    await CreateDocSkill(config=cfg).execute("u1", {"name": "Letter"})
    await SetDocContentSkill(config=cfg).execute("u1", {
        "doc_id": "Letter", "text": "Dear hiring manager,",
    })
    out = await ReadDocSkill(config=cfg).execute("u1", {"doc_id": "Letter"})
    assert "Dear hiring manager," in out

    await AppendToDocSkill(config=cfg).execute("u1", {
        "doc_id": "Letter", "text": "Thank you for your time.",
    })
    out = await ReadDocSkill(config=cfg).execute("u1", {"doc_id": "Letter"})
    assert "Dear hiring manager," in out
    assert "Thank you for your time." in out


async def test_set_doc_content_replaces_whole_body(cfg):
    await CreateDocSkill(config=cfg).execute("u1", {"name": "Memo"})
    await SetDocContentSkill(config=cfg).execute("u1", {
        "doc_id": "Memo", "text": "First version.",
    })
    out = await SetDocContentSkill(config=cfg).execute("u1", {
        "doc_id": "Memo", "text": "Totally new body.\nWith two lines.",
    })
    assert "2 paragraph" in out
    read = await ReadDocSkill(config=cfg).execute("u1", {"doc_id": "Memo"})
    assert "First version." not in read
    assert "Totally new body." in read
    assert "With two lines." in read


async def test_append_to_blank_doc(cfg):
    await CreateDocSkill(config=cfg).execute("u1", {"name": "Notes"})
    await AppendToDocSkill(config=cfg).execute("u1", {
        "doc_id": "Notes", "text": "One line only",
    })
    read = await ReadDocSkill(config=cfg).execute("u1", {"doc_id": "Notes"})
    # No leading blank paragraph — append on blank replaces the empty line.
    assert "One line only" in read


async def test_read_doc_no_docs(cfg):
    out = await ReadDocSkill(config=cfg).execute("u1", {})
    assert "no docs" in out.lower()


async def test_append_requires_text(cfg):
    await CreateDocSkill(config=cfg).execute("u1", {"name": "X"})
    out = await AppendToDocSkill(config=cfg).execute("u1", {"doc_id": "X", "text": "   "})
    assert "text" in out.lower()


async def test_resolve_by_id_name_substring_and_missing(cfg):
    created = await CreateDocSkill(config=cfg).execute("u1", {"name": "Quarterly Report"})
    # id appears in the create message inside backticks
    did = created.split("`")[1]

    by_id, err = await _resolve_doc_id(cfg, "u1", did)
    assert by_id == did and err is None

    by_name, err = await _resolve_doc_id(cfg, "u1", "quarterly report")
    assert by_name == did and err is None

    by_sub, err = await _resolve_doc_id(cfg, "u1", "quarter")
    assert by_sub == did and err is None

    missing, err = await _resolve_doc_id(cfg, "u1", "nonexistent")
    assert missing is None and err is not None


async def test_send_doc_delivers_via_telegram(cfg, monkeypatch):
    await CreateDocSkill(config=cfg).execute("u1", {"name": "Letter"})
    await SetDocContentSkill(config=cfg).execute("u1", {
        "doc_id": "Letter", "text": "Hi there.",
    })

    import lazyclaw.notifications.push as push_mod
    captured: dict = {}

    async def fake_push(config, content, filename, *, caption=None):
        captured["filename"] = filename
        captured["bytes"] = len(content)
        captured["caption"] = caption
        return True

    monkeypatch.setattr(push_mod, "push_telegram_document", fake_push)
    out = await SendDocSkill(config=cfg).execute("u1", {"doc_id": "Letter"})
    assert captured["filename"] == "Letter.docx"
    assert captured["bytes"] > 0
    assert "Sent" in out and "Letter.docx" in out


async def test_send_doc_fallback_when_not_configured(cfg, monkeypatch):
    await CreateDocSkill(config=cfg).execute("u1", {"name": "Report"})

    import lazyclaw.notifications.push as push_mod

    async def fake_push(config, content, filename, *, caption=None):
        return False  # Telegram not configured

    monkeypatch.setattr(push_mod, "push_telegram_document", fake_push)
    out = await SendDocSkill(config=cfg).execute("u1", {"doc_id": "Report"})
    assert "Report.docx" in out
    assert "/api/docs/" in out and "format=docx" in out


async def test_permission_default_is_allow():
    assert DEFAULT_CATEGORY_PERMISSIONS.get("docs") == ALLOW


async def test_skill_metadata():
    assert CreateDocSkill().category == "docs"
    assert ListDocsSkill().read_only is True
    assert ReadDocSkill().read_only is True
    assert AppendToDocSkill().read_only is False
    assert SetDocContentSkill().read_only is False
    # names are stable tool identifiers
    assert {
        CreateDocSkill().name, ListDocsSkill().name, ReadDocSkill().name,
        AppendToDocSkill().name, SetDocContentSkill().name, SendDocSkill().name,
    } == {
        "create_doc", "list_docs", "read_doc", "append_to_doc",
        "set_doc_content", "send_doc",
    }
