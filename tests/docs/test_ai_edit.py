"""Deterministic apply for the Docs AI specialist (lazyclaw/docs/ai_edit.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.docs import ai_edit, snapshot as D
from lazyclaw.docs.store import create_doc, get_doc, save_doc

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


async def _doc(cfg, text="Hello"):
    row = await create_doc(cfg, "u1", "Doc")
    snap = D.set_text(D.blank_document("Doc", doc_id=row["id"]), text)
    await save_doc(cfg, "u1", "Doc", snap, doc_id=row["id"])
    return row["id"]


async def test_load_missing_returns_none(cfg):
    assert await ai_edit.load(cfg, "u1", "nope") is None


async def test_build_messages_embeds_text(cfg):
    did = await _doc(cfg, "Body text")
    ctx = await ai_edit.load(cfg, "u1", did)
    msgs = ai_edit.build_messages(ctx, "add a closing")
    assert len(msgs) == 2 and msgs[0].role == "system"
    assert "Body text" in msgs[1].content
    assert "add a closing" in msgs[1].content


async def test_apply_append_with_link(cfg):
    did = await _doc(cfg, "Intro")
    ctx = await ai_edit.load(cfg, "u1", did)
    plan = {
        "mode": "append",
        "paragraphs": [
            {"runs": [{"text": "See "}, {"text": "my site", "url": "https://x.io"}]}
        ],
    }
    res = await ai_edit.apply(cfg, "u1", did, ctx, plan)
    assert res["new_id"] is None
    runs = [r for para in D.get_paragraph_runs(res["snapshot"]) for r in para]
    assert {"text": "my site", "url": "https://x.io"} in runs
    assert "Intro" in D.get_text(res["snapshot"])  # append preserved original


async def test_apply_replace_mode(cfg):
    did = await _doc(cfg, "Old body")
    ctx = await ai_edit.load(cfg, "u1", did)
    plan = {"mode": "replace", "paragraphs": ["Brand new", "second line"]}
    res = await ai_edit.apply(cfg, "u1", did, ctx, plan)
    assert D.get_paragraphs(res["snapshot"]) == ["Brand new", "second line"]
    assert "Old body" not in D.get_text(res["snapshot"])


async def test_apply_markdown_paragraph_string(cfg):
    did = await _doc(cfg)
    ctx = await ai_edit.load(cfg, "u1", did)
    plan = {"paragraphs": ["Visit [the site](https://x.io) now"]}
    res = await ai_edit.apply(cfg, "u1", did, ctx, plan)
    runs = [r for para in D.get_paragraph_runs(res["snapshot"]) for r in para]
    assert {"text": "the site", "url": "https://x.io"} in runs


async def test_apply_rejects_empty_plan(cfg):
    did = await _doc(cfg)
    ctx = await ai_edit.load(cfg, "u1", did)
    with pytest.raises(ValueError):
        await ai_edit.apply(cfg, "u1", did, ctx, {"paragraphs": []})
