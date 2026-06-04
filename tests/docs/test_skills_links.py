"""append_to_doc hyperlink support (lazyclaw/skills/builtin/docs.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.docs import snapshot as D
from lazyclaw.docs.store import get_doc, list_docs
from lazyclaw.skills.builtin.docs import AppendToDocSkill, CreateDocSkill

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


async def _only_doc(cfg):
    rows = await list_docs(cfg, "u1")
    return await get_doc(cfg, "u1", rows[0]["id"])


async def test_append_with_link_url_and_text(cfg):
    await CreateDocSkill(config=cfg).execute("u1", {"name": "CL"})
    out = await AppendToDocSkill(config=cfg).execute(
        "u1",
        {"doc_id": "CL", "text": "Check my site here", "link_text": "site", "link_url": "https://x.io"},
    )
    assert "link" in out
    doc = await _only_doc(cfg)
    runs = [r for para in D.get_paragraph_runs(doc["payload"]) for r in para]
    assert {"text": "site", "url": "https://x.io"} in runs
    assert "Check my site here" in D.get_text(doc["payload"])


async def test_append_with_markdown_link(cfg):
    await CreateDocSkill(config=cfg).execute("u1", {"name": "MD"})
    await AppendToDocSkill(config=cfg).execute(
        "u1", {"doc_id": "MD", "text": "See [my portfolio](https://p.io) now"}
    )
    doc = await _only_doc(cfg)
    runs = [r for para in D.get_paragraph_runs(doc["payload"]) for r in para]
    assert {"text": "my portfolio", "url": "https://p.io"} in runs


async def test_append_plain_text_unchanged(cfg):
    await CreateDocSkill(config=cfg).execute("u1", {"name": "P"})
    out = await AppendToDocSkill(config=cfg).execute(
        "u1", {"doc_id": "P", "text": "just a line"}
    )
    assert "link" not in out
    doc = await _only_doc(cfg)
    assert D.get_text(doc["payload"]) == "just a line"
