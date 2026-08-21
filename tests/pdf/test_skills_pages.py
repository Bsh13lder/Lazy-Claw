"""PDF page-surgery + table-extraction skills.

`ops.py` has carried rotate / delete_pages / flatten / extract_tables since the
Documents Workspace shipped, but no skill wrapped them — rotate was reachable
only from the ✨ plan, the other three from nowhere. These cover the wrappers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.pdf import store
from lazyclaw.skills.builtin.pdf_pages import (
    DeletePdfPagesSkill,
    ExtractPdfTablesSkill,
    FlattenPdfSkill,
    RotatePdfSkill,
    _page_list,
)
from tests.pdf.conftest import make_multipage_pdf, make_text_pdf

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


@pytest.fixture
async def pages(cfg):
    """A 3-page PDF. Returns its id."""
    row = await store.save_pdf(cfg, "u1", "Scan.pdf", make_multipage_pdf(3))
    return row["id"]


async def _page_count(cfg, name_fragment: str) -> int:
    rows = await store.list_pdfs(cfg, "u1")
    return [r for r in rows if name_fragment in r["name"]][0]["pages"]


# ───────────────────────── page-list coercion ───────────────────────

async def test_page_list_distinguishes_omitted_from_malformed():
    """Three-way: None means 'all pages', False means 'reject'.

    Collapsing these would rotate an entire document when the user asked for
    one page — a destructive surprise, so it must be reported instead.
    """
    assert _page_list(None) is None
    assert _page_list([1, 3]) == [1, 3]
    assert _page_list(["2"]) == [2]
    assert _page_list("nope") is False
    assert _page_list([0]) is False
    assert _page_list([-1]) is False
    assert _page_list([True]) is False
    assert _page_list([1.5]) is False


# ───────────────────────── rotate ───────────────────────────────────

async def test_rotate_saves_a_new_pdf(cfg, pages):
    out = await RotatePdfSkill(config=cfg).execute(
        "u1", {"pdf_id": pages, "degrees": 90}
    )
    assert "90" in out and "id `" in out
    rows = await store.list_pdfs(cfg, "u1")
    assert len(rows) == 2, "PDFs are immutable — the original must survive"


async def test_rotate_named_pages_only(cfg, pages):
    out = await RotatePdfSkill(config=cfg).execute(
        "u1", {"pdf_id": pages, "degrees": 180, "pages": [2]}
    )
    assert "page(s) 2" in out


async def test_rotate_rejects_a_bad_angle(cfg, pages):
    out = await RotatePdfSkill(config=cfg).execute(
        "u1", {"pdf_id": pages, "degrees": 45}
    )
    assert "90" in out and "270" in out
    assert len(await store.list_pdfs(cfg, "u1")) == 1, "nothing should be saved"


async def test_rotate_rejects_a_malformed_page_list(cfg, pages):
    out = await RotatePdfSkill(config=cfg).execute(
        "u1", {"pdf_id": pages, "degrees": 90, "pages": "all of them"}
    )
    assert "1-based" in out


# ───────────────────────── delete pages ─────────────────────────────

async def test_delete_pages_drops_them(cfg, pages):
    out = await DeletePdfPagesSkill(config=cfg).execute(
        "u1", {"pdf_id": pages, "pages": [2]}
    )
    assert "id `" in out
    # make_multipage_pdf guarantees "at least n" pages, so compare relatively.
    before = await _page_count(cfg, "Scan.pdf")
    after = await _page_count(cfg, "pages removed")
    assert after == before - 1


async def test_delete_pages_requires_a_page_list(cfg, pages):
    out = await DeletePdfPagesSkill(config=cfg).execute("u1", {"pdf_id": pages})
    assert "non-empty" in out


async def test_delete_pages_reports_an_out_of_range_page(cfg, pages):
    out = await DeletePdfPagesSkill(config=cfg).execute(
        "u1", {"pdf_id": pages, "pages": [99]}
    )
    assert "could not" in out.lower() or "page" in out.lower()


# ───────────────────────── flatten ──────────────────────────────────

async def test_flatten_saves_a_new_pdf(cfg, pages):
    out = await FlattenPdfSkill(config=cfg).execute("u1", {"pdf_id": pages})
    assert "flattened" in out.lower()
    assert len(await store.list_pdfs(cfg, "u1")) == 2


# ───────────────────────── extract tables ───────────────────────────

async def test_extract_tables_reports_when_there_are_none(cfg):
    await store.save_pdf(cfg, "u1", "Prose.pdf", make_text_pdf("just words"))
    out = await ExtractPdfTablesSkill(config=cfg).execute("u1", {})
    assert "no tables" in out.lower()
    assert "read_pdf" in out, "should point at the tool that WILL work"


async def test_extract_tables_is_read_only(cfg, pages):
    assert ExtractPdfTablesSkill(config=cfg).read_only is True
    await ExtractPdfTablesSkill(config=cfg).execute("u1", {"pdf_id": pages})
    assert len(await store.list_pdfs(cfg, "u1")) == 1


# ───────────────────────── shared behaviour ─────────────────────────

async def test_every_skill_reports_when_the_user_has_no_pdfs(cfg):
    for skill in (
        RotatePdfSkill(config=cfg),
        FlattenPdfSkill(config=cfg),
        ExtractPdfTablesSkill(config=cfg),
    ):
        params = {"degrees": 90} if skill.name == "rotate_pdf" else {}
        out = await skill.execute("u1", params)
        assert "no pdfs" in out.lower(), skill.name


async def test_skills_are_in_the_pdf_category(cfg):
    for skill in (
        RotatePdfSkill(config=cfg), DeletePdfPagesSkill(config=cfg),
        FlattenPdfSkill(config=cfg), ExtractPdfTablesSkill(config=cfg),
    ):
        assert skill.category == "pdf", skill.name
