"""Tests for the agent PDF skills (lazyclaw/skills/builtin/pdf.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.permissions.models import ALLOW, DEFAULT_CATEGORY_PERMISSIONS
from lazyclaw.pdf import ops, store
from lazyclaw.skills.builtin.pdf import (
    AddTextToPdfSkill,
    FillPdfFormSkill,
    GeneratePdfSkill,
    ListPdfsSkill,
    MergePdfsSkill,
    ReadPdfSkill,
    SendPdfSkill,
    SplitPdfSkill,
    _resolve_pdf_id,
)
from tests.pdf.conftest import make_form_pdf, make_multipage_pdf, make_text_pdf

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


def _id_from(msg: str) -> str:
    return msg.split("`")[1]


# ── generate / list / read ──────────────────────────────────────────────────

async def test_generate_then_list_and_read(cfg):
    out = await GeneratePdfSkill(config=cfg).execute(
        "u1", {"text": "Sentinel content here.", "title": "Memo", "name": "memo"}
    )
    assert "Generated" in out and "memo.pdf" in out

    listed = await ListPdfsSkill(config=cfg).execute("u1", {})
    assert "memo.pdf" in listed

    read = await ReadPdfSkill(config=cfg).execute("u1", {"pdf_id": "memo"})
    assert "Sentinel content here" in read


async def test_generate_requires_text(cfg):
    out = await GeneratePdfSkill(config=cfg).execute("u1", {"text": "   "})
    assert "text" in out.lower()


async def test_read_no_pdfs(cfg):
    out = await ReadPdfSkill(config=cfg).execute("u1", {})
    assert "no pdfs" in out.lower()


# ── merge / split ───────────────────────────────────────────────────────────

async def test_merge_pdfs(cfg):
    await store.save_pdf(cfg, "u1", "a.pdf", make_text_pdf("A"))
    await store.save_pdf(cfg, "u1", "b.pdf", make_text_pdf("B"))
    out = await MergePdfsSkill(config=cfg).execute("u1", {"pdf_ids": ["a.pdf", "b.pdf"], "name": "ab.pdf"})
    assert "Merged 2 PDFs" in out
    new_id = _id_from(out)
    fetched = await store.get_pdf(cfg, "u1", new_id)
    assert ops.page_count(fetched["bytes"]) == 2


async def test_merge_needs_two(cfg):
    await store.save_pdf(cfg, "u1", "a.pdf", make_text_pdf("A"))
    out = await MergePdfsSkill(config=cfg).execute("u1", {"pdf_ids": ["a.pdf"]})
    assert "at least two" in out.lower()


async def test_split_pdf_per_page(cfg):
    meta = await store.save_pdf(cfg, "u1", "big.pdf", make_multipage_pdf(3))
    out = await SplitPdfSkill(config=cfg).execute("u1", {"pdf_id": meta["id"]})
    assert "Split into" in out
    # original + parts now in the store
    rows = await store.list_pdfs(cfg, "u1")
    assert len(rows) >= 4  # 1 source + >=3 parts


async def test_split_pdf_ranges(cfg):
    meta = await store.save_pdf(cfg, "u1", "big.pdf", make_multipage_pdf(3))
    out = await SplitPdfSkill(config=cfg).execute(
        "u1", {"pdf_id": meta["id"], "ranges": [[1, 2]]}
    )
    assert "Split into 1" in out


# ── fill form ───────────────────────────────────────────────────────────────

async def test_fill_pdf_form(cfg):
    meta = await store.save_pdf(cfg, "u1", "form.pdf", make_form_pdf())
    out = await FillPdfFormSkill(config=cfg).execute(
        "u1", {"pdf_id": meta["id"], "values": {"full_name": "Alice Smith"}}
    )
    assert "Filled" in out
    new_id = _id_from(out)
    fetched = await store.get_pdf(cfg, "u1", new_id)
    assert ops.get_form_fields(fetched["bytes"]).get("full_name") == "Alice Smith"


async def test_fill_pdf_form_no_fields(cfg):
    meta = await store.save_pdf(cfg, "u1", "plain.pdf", make_text_pdf())
    out = await FillPdfFormSkill(config=cfg).execute(
        "u1", {"pdf_id": meta["id"], "values": {"x": "y"}}
    )
    assert "no fillable form fields" in out.lower()


async def test_fill_pdf_form_requires_values(cfg):
    await store.save_pdf(cfg, "u1", "form.pdf", make_form_pdf())
    out = await FillPdfFormSkill(config=cfg).execute("u1", {"pdf_id": "form.pdf", "values": {}})
    assert "values" in out.lower()


# ── add text / sign ─────────────────────────────────────────────────────────

async def test_add_text_to_pdf(cfg):
    meta = await store.save_pdf(cfg, "u1", "doc.pdf", make_text_pdf())
    out = await AddTextToPdfSkill(config=cfg).execute(
        "u1",
        {"pdf_id": meta["id"], "items": [{"page": 1, "x": 72, "y": 72, "text": "SIGNED"}]},
    )
    assert "Stamped" in out
    new_id = _id_from(out)
    fetched = await store.get_pdf(cfg, "u1", new_id)
    assert fetched["bytes"][:4] == b"%PDF"


async def test_add_text_requires_items(cfg):
    await store.save_pdf(cfg, "u1", "doc.pdf", make_text_pdf())
    out = await AddTextToPdfSkill(config=cfg).execute("u1", {"pdf_id": "doc.pdf", "items": []})
    assert "items" in out.lower()


# ── send (telegram + fallback) ──────────────────────────────────────────────

async def test_send_pdf_via_telegram(cfg, monkeypatch):
    await GeneratePdfSkill(config=cfg).execute("u1", {"text": "x", "name": "contract"})

    import lazyclaw.notifications.push as push_mod
    captured: dict = {}

    async def fake_push(config, content, filename, *, caption=None):
        captured["filename"] = filename
        captured["bytes"] = len(content)
        captured["caption"] = caption
        return True

    monkeypatch.setattr(push_mod, "push_telegram_document", fake_push)
    out = await SendPdfSkill(config=cfg).execute("u1", {"pdf_id": "contract"})
    assert captured["filename"] == "contract.pdf"
    assert captured["bytes"] > 0
    assert "Sent" in out and "contract.pdf" in out


async def test_send_pdf_fallback_when_not_configured(cfg, monkeypatch):
    await GeneratePdfSkill(config=cfg).execute("u1", {"text": "x", "name": "report"})

    import lazyclaw.notifications.push as push_mod

    async def fake_push(config, content, filename, *, caption=None):
        return False

    monkeypatch.setattr(push_mod, "push_telegram_document", fake_push)
    out = await SendPdfSkill(config=cfg).execute("u1", {"pdf_id": "report"})
    assert "report.pdf" in out
    assert "/api/pdf/" in out and "/download" in out


# ── resolve helper ──────────────────────────────────────────────────────────

async def test_resolve_by_id_name_substring_and_missing(cfg):
    meta = await store.save_pdf(cfg, "u1", "Quarterly Report.pdf", make_text_pdf())
    sid = meta["id"]

    by_id, err = await _resolve_pdf_id(cfg, "u1", sid)
    assert by_id == sid and err is None

    by_name, err = await _resolve_pdf_id(cfg, "u1", "quarterly report.pdf")
    assert by_name == sid and err is None

    by_sub, err = await _resolve_pdf_id(cfg, "u1", "quarter")
    assert by_sub == sid and err is None

    missing, err = await _resolve_pdf_id(cfg, "u1", "nonexistent")
    assert missing is None and err is not None


# ── metadata / permissions ──────────────────────────────────────────────────

async def test_permission_default_is_allow():
    assert DEFAULT_CATEGORY_PERMISSIONS.get("pdf") == ALLOW


async def test_skill_metadata():
    assert ListPdfsSkill().category == "pdf"
    assert ListPdfsSkill().read_only is True
    assert ReadPdfSkill().read_only is True
    assert MergePdfsSkill().read_only is False
    assert {
        ListPdfsSkill().name, ReadPdfSkill().name, MergePdfsSkill().name,
        SplitPdfSkill().name, FillPdfFormSkill().name, AddTextToPdfSkill().name,
        GeneratePdfSkill().name, SendPdfSkill().name,
    } == {
        "list_pdfs", "read_pdf", "merge_pdfs", "split_pdf",
        "fill_pdf_form", "add_text_to_pdf", "generate_pdf", "send_pdf",
    }
