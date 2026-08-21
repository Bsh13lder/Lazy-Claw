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


# ───────────── targeted edit + alignment (2026-08-21) ────────────────


async def test_edit_mode_replaces_one_block_and_leaves_the_rest(cfg):
    """"Make the third paragraph bold" without rewriting the document."""
    row = await create_doc(cfg, "u1", "Notes")
    ctx = await ai_edit.load(cfg, "u1", row["id"])
    body = D.build_body_with_blocks([
        {"type": "heading", "level": 1, "runs": [{"text": "Title"}]},
        {"type": "paragraph", "level": 0, "runs": [{"text": "first"}]},
        {"type": "paragraph", "level": 0, "runs": [{"text": "second"}]},
    ])
    ctx = {**ctx, "payload": {**ctx["payload"], "body": body}}

    out = await ai_edit.apply(cfg, "u1", row["id"], ctx, {
        "mode": "edit",
        "index": 2,
        "blocks": [{"type": "paragraph", "text": "**rewritten**"}],
    })

    blocks = D.get_blocks(out["snapshot"])
    assert len(blocks) == 3, "editing one block must not add or drop blocks"
    assert blocks[0]["type"] == "heading", "the heading survived untouched"
    assert blocks[1]["runs"][0]["text"] == "first"
    assert blocks[2]["runs"][0]["text"] == "rewritten"
    assert blocks[2]["runs"][0].get("bold") is True


async def test_edit_mode_rejects_an_out_of_range_index(cfg):
    row = await create_doc(cfg, "u1", "Notes")
    ctx = await ai_edit.load(cfg, "u1", row["id"])
    with pytest.raises(IndexError):
        await ai_edit.apply(cfg, "u1", row["id"], ctx, {
            "mode": "edit", "index": 99,
            "blocks": [{"type": "paragraph", "text": "x"}],
        })


async def test_edit_mode_without_an_index_is_a_clear_error(cfg):
    row = await create_doc(cfg, "u1", "Notes")
    ctx = await ai_edit.load(cfg, "u1", row["id"])
    with pytest.raises(ValueError, match="index"):
        await ai_edit.apply(cfg, "u1", row["id"], ctx, {
            "mode": "edit", "blocks": [{"type": "paragraph", "text": "x"}],
        })


async def test_alignment_round_trips_through_the_snapshot(cfg):
    row = await create_doc(cfg, "u1", "Notes")
    ctx = await ai_edit.load(cfg, "u1", row["id"])
    out = await ai_edit.apply(cfg, "u1", row["id"], ctx, {
        "blocks": [
            {"type": "heading", "level": 1, "text": "Centred", "align": "center"},
            {"type": "paragraph", "text": "Right", "align": "right"},
            {"type": "paragraph", "text": "Default"},
        ],
    })
    blocks = D.get_blocks(out["snapshot"])
    assert blocks[0]["align"] == "center"
    assert blocks[1]["align"] == "right"
    assert "align" not in blocks[2]


async def test_alignment_does_not_clobber_a_headings_level(cfg):
    """Both ride in `paragraphStyle` — assigning instead of merging would
    demote every centred heading to body text."""
    row = await create_doc(cfg, "u1", "Notes")
    ctx = await ai_edit.load(cfg, "u1", row["id"])
    out = await ai_edit.apply(cfg, "u1", row["id"], ctx, {
        "blocks": [{"type": "heading", "level": 2, "text": "T", "align": "center"}],
    })
    block = D.get_blocks(out["snapshot"])[0]
    assert block["type"] == "heading" and block["level"] == 2
    assert block["align"] == "center"


async def test_a_bogus_alignment_is_ignored_not_fatal(cfg):
    row = await create_doc(cfg, "u1", "Notes")
    ctx = await ai_edit.load(cfg, "u1", row["id"])
    out = await ai_edit.apply(cfg, "u1", row["id"], ctx, {
        "blocks": [{"type": "paragraph", "text": "x", "align": "diagonal"}],
    })
    assert "align" not in D.get_blocks(out["snapshot"])[0]
