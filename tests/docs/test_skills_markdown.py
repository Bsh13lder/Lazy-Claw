"""Markdown-aware doc skills: real lists/headings/emphasis (Task A5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.docs import snapshot as D
from lazyclaw.docs.store import get_doc
from lazyclaw.skills.builtin.docs import (
    AppendToDocSkill,
    CreateDocSkill,
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


async def _blocks(cfg, name):
    did, _ = await _resolve_doc_id(cfg, "u1", name)
    doc = await get_doc(cfg, "u1", did)
    return D.get_blocks(doc["payload"])


async def test_append_markdown_numbered_list(cfg):
    await CreateDocSkill(config=cfg).execute("u1", {"name": "Plan"})
    await AppendToDocSkill(config=cfg).execute(
        "u1", {"doc_id": "Plan", "markdown": "1. a\n2. b"}
    )
    blocks = await _blocks(cfg, "Plan")
    types = [b["type"] for b in blocks if b["runs"] and b["runs"][0]["text"]]
    assert types[-2:] == ["number", "number"]


async def test_set_doc_content_markdown_heading_and_bullets(cfg):
    await CreateDocSkill(config=cfg).execute("u1", {"name": "Doc"})
    await SetDocContentSkill(config=cfg).execute(
        "u1", {"doc_id": "Doc", "markdown": "# Title\n- x\n- y"}
    )
    blocks = await _blocks(cfg, "Doc")
    real = [b for b in blocks if b["runs"] and b["runs"][0]["text"]]
    assert real[0]["type"] == "heading"
    assert [b["type"] for b in real[1:]] == ["bullet", "bullet"]


async def test_append_markdown_bold_survives(cfg):
    await CreateDocSkill(config=cfg).execute("u1", {"name": "B"})
    await AppendToDocSkill(config=cfg).execute(
        "u1", {"doc_id": "B", "markdown": "this is **strong** text"}
    )
    blocks = await _blocks(cfg, "B")
    runs = [r for b in blocks for r in b["runs"]]
    assert any(r.get("bold") for r in runs)


async def test_plain_text_append_still_works(cfg):
    await CreateDocSkill(config=cfg).execute("u1", {"name": "P"})
    out = await AppendToDocSkill(config=cfg).execute(
        "u1", {"doc_id": "P", "text": "plain paragraph"}
    )
    assert "Appended" in out
