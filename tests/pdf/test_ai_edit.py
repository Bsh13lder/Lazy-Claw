"""Deterministic apply for the PDF AI specialist (lazyclaw/pdf/ai_edit.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.pdf import ai_edit, ops
from lazyclaw.pdf.store import get_pdf, list_pdf_versions, list_pdfs, save_pdf

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


async def _pdf(cfg, text="Hello world.\n\nSecond paragraph."):
    data = ops.generate_from_text(text, title="Doc")
    row = await save_pdf(cfg, "u1", "doc.pdf", data)
    return row["id"]


async def test_load_and_build_messages(cfg):
    pid = await _pdf(cfg)
    ctx = await ai_edit.load(cfg, "u1", pid)
    assert ctx is not None
    msgs = ai_edit.build_messages(ctx, "add a signature")
    assert len(msgs) == 2
    assert "add a signature" in msgs[1].content


async def test_apply_add_text_edits_in_place(cfg):
    pid = await _pdf(cfg)
    ctx = await ai_edit.load(cfg, "u1", pid)
    assert ctx is not None
    plan = {"op": "add_text", "items": [{"page": 1, "x": 72, "y": 700, "text": "Signed"}]}
    res = await ai_edit.apply(cfg, "u1", pid, ctx, plan)
    # In-place: same id, viewer reloads THIS file — no fork in the sidebar.
    assert res["new_id"] == pid
    assert res["snapshot"] is None
    live = await get_pdf(cfg, "u1", pid)
    assert live is not None and ops.is_pdf(live["bytes"])
    assert len(await list_pdfs(cfg, "u1")) == 1
    # The pre-edit bytes are recoverable as a hidden version.
    assert len(await list_pdf_versions(cfg, "u1", pid)) == 1


async def test_apply_fill_form_edits_in_place(cfg):
    from tests.pdf.conftest import make_form_pdf

    row = await save_pdf(cfg, "u1", "form.pdf", make_form_pdf("full_name"))
    pid = row["id"]
    ctx = await ai_edit.load(cfg, "u1", pid)
    assert ctx is not None
    res = await ai_edit.apply(
        cfg, "u1", pid, ctx, {"op": "fill_form", "values": {"full_name": "Ada"}}
    )
    assert res["new_id"] == pid
    assert len(await list_pdfs(cfg, "u1")) == 1


async def test_apply_split_still_creates_new_files(cfg):
    from tests.pdf.conftest import make_multipage_pdf

    row = await save_pdf(cfg, "u1", "multi.pdf", make_multipage_pdf(3))
    pid = row["id"]
    ctx = await ai_edit.load(cfg, "u1", pid)
    assert ctx is not None
    res = await ai_edit.apply(cfg, "u1", pid, ctx, {"op": "split"})
    # split is inherently 1 → N; it must NOT collapse onto the open file.
    assert res["new_id"] is not None and res["new_id"] != pid


async def test_apply_generate(cfg):
    pid = await _pdf(cfg)
    ctx = await ai_edit.load(cfg, "u1", pid)
    res = await ai_edit.apply(
        cfg, "u1", pid, ctx, {"op": "generate", "text": "Fresh content", "title": "New"}
    )
    assert res["new_id"]
    new = await get_pdf(cfg, "u1", res["new_id"])
    assert "Fresh content" in ops.extract_text(new["bytes"])


async def test_apply_unknown_op_raises(cfg):
    pid = await _pdf(cfg)
    ctx = await ai_edit.load(cfg, "u1", pid)
    with pytest.raises(ValueError):
        await ai_edit.apply(cfg, "u1", pid, ctx, {"op": "frobnicate"})
