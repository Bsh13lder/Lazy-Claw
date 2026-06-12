"""Tests for conflict-aware save + tags in sheets/docs/pdf stores.

TDD: this file was written BEFORE the implementation.

Covers:
- SheetConflictError raised when base_updated_at is stale
- SheetConflictError.current carries the decrypted fresh row
- No error when base_updated_at matches
- No error when base_updated_at is omitted (last-write-wins)
- save_sheet(name=None) preserves the stored name
- tags round-trip through save_sheet / list_sheets / get_sheet
- _clean_tags sanitisation (non-list, >32 tags, >40 chars, duplicates)
- DocConflictError mirrors SheetConflictError for docs store
- update_pdf_meta sets name/tags without touching payload
- cross-user isolation: save_sheet by u2 does not touch u1's row
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.sheets import store as sheets_store
from lazyclaw.docs import store as docs_store
from lazyclaw.pdf import store as pdf_store

pytestmark = pytest.mark.asyncio


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        for uid, salt in (("u1", "salt-a"), ("u2", "salt-b")):
            await db.execute(
                "INSERT INTO users (id, username, password_hash, encryption_salt) "
                "VALUES (?, ?, ?, ?)",
                (uid, uid, "x", salt),
            )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _make_sheet(cfg, user_id: str = "u1", name: str = "Sheet"):
    return await sheets_store.create_sheet(cfg, user_id, name)


async def _make_doc(cfg, user_id: str = "u1", name: str = "Doc"):
    return await docs_store.create_doc(cfg, user_id, name)


# ── sheets_store: conflict detection ─────────────────────────────────────────


async def test_stale_base_raises_conflict(cfg):
    """save_sheet with a stale base_updated_at raises SheetConflictError."""
    row = await _make_sheet(cfg)
    sid = row["id"]
    sheet = await sheets_store.get_sheet(cfg, "u1", sid)
    assert sheet is not None
    snap = sheet["payload"]

    # Advance the stored updated_at directly in the DB (bypasses 1-second resolution)
    newer_ts = "2026-01-01 12:00:01"
    async with db_session(cfg) as db:
        await db.execute(
            "UPDATE sheets SET updated_at = ? WHERE id = ?", (newer_ts, sid)
        )
        await db.commit()

    # base_updated_at still holds the original (stale) timestamp
    with pytest.raises(sheets_store.SheetConflictError) as exc_info:
        await sheets_store.save_sheet(
            cfg, "u1", "Sheet", snap,
            sheet_id=sid,
            base_updated_at=row["updated_at"],  # stale
        )

    err = exc_info.value
    assert hasattr(err, "current"), "SheetConflictError must carry .current"
    assert err.current["id"] == sid
    assert "payload" in err.current, ".current must include decrypted payload"
    assert err.current["updated_at"] == newer_ts


async def test_fresh_base_does_not_raise(cfg):
    """save_sheet with the matching base_updated_at succeeds."""
    row = await _make_sheet(cfg)
    sid = row["id"]
    sheet = await sheets_store.get_sheet(cfg, "u1", sid)
    assert sheet is not None
    snap = sheet["payload"]

    # Must not raise
    result = await sheets_store.save_sheet(
        cfg, "u1", "Sheet", snap,
        sheet_id=sid,
        base_updated_at=row["updated_at"],
    )
    assert result["id"] == sid


async def test_no_base_updated_at_is_last_write_wins(cfg):
    """Omitting base_updated_at keeps last-write-wins with no conflict error."""
    row = await _make_sheet(cfg)
    sid = row["id"]
    sheet = await sheets_store.get_sheet(cfg, "u1", sid)
    assert sheet is not None
    snap = sheet["payload"]

    # Save twice without passing base_updated_at — must not raise
    await sheets_store.save_sheet(cfg, "u1", "Sheet", snap, sheet_id=sid)
    await sheets_store.save_sheet(cfg, "u1", "Sheet", snap, sheet_id=sid)


# ── sheets_store: name preservation ───────────────────────────────────────────


async def test_save_sheet_none_name_preserves_stored_name(cfg):
    """Passing name=None keeps the existing stored name."""
    row = await _make_sheet(cfg, name="Budget 2026")
    sid = row["id"]
    sheet = await sheets_store.get_sheet(cfg, "u1", sid)
    assert sheet is not None
    snap = sheet["payload"]

    result = await sheets_store.save_sheet(
        cfg, "u1", None, snap, sheet_id=sid
    )
    assert result["name"] == "Budget 2026"


async def test_save_sheet_new_name_renames(cfg):
    """Passing a non-None name updates the stored name."""
    row = await _make_sheet(cfg, name="Old Name")
    sid = row["id"]
    sheet = await sheets_store.get_sheet(cfg, "u1", sid)
    assert sheet is not None
    snap = sheet["payload"]

    result = await sheets_store.save_sheet(
        cfg, "u1", "New Name", snap, sheet_id=sid
    )
    assert result["name"] == "New Name"


# ── sheets_store: tags ────────────────────────────────────────────────────────


async def test_tags_round_trip(cfg):
    """Tags saved via save_sheet appear in list_sheets and get_sheet."""
    row = await _make_sheet(cfg)
    sid = row["id"]
    sheet = await sheets_store.get_sheet(cfg, "u1", sid)
    assert sheet is not None
    snap = sheet["payload"]

    await sheets_store.save_sheet(
        cfg, "u1", None, snap, sheet_id=sid, tags=["finance", "2026"]
    )

    listing = await sheets_store.list_sheets(cfg, "u1")
    assert listing[0]["tags"] == ["finance", "2026"]

    fetched = await sheets_store.get_sheet(cfg, "u1", sid)
    assert fetched is not None
    assert fetched["tags"] == ["finance", "2026"]


async def test_tags_default_empty_list(cfg):
    """A newly created sheet (no tags) returns tags=[] in list and get."""
    row = await _make_sheet(cfg)
    listing = await sheets_store.list_sheets(cfg, "u1")
    assert listing[0]["tags"] == []
    fetched = await sheets_store.get_sheet(cfg, "u1", row["id"])
    assert fetched is not None
    assert fetched["tags"] == []


async def test_clean_tags_non_list_returns_empty():
    """_clean_tags rejects non-list input."""
    assert sheets_store._clean_tags("not a list") == []
    assert sheets_store._clean_tags(None) == []
    assert sheets_store._clean_tags(42) == []


async def test_clean_tags_truncates_long_tag():
    """_clean_tags truncates tags longer than 40 chars."""
    long_tag = "a" * 50
    result = sheets_store._clean_tags([long_tag])
    assert len(result) == 1
    assert len(result[0]) == 40


async def test_clean_tags_deduplicates():
    """_clean_tags removes duplicate tags."""
    result = sheets_store._clean_tags(["foo", "bar", "foo"])
    assert result == ["foo", "bar"]


async def test_clean_tags_max_32():
    """_clean_tags caps the list at 32 tags."""
    many = [str(i) for i in range(50)]
    result = sheets_store._clean_tags(many)
    assert len(result) == 32


async def test_save_sheet_with_no_tags_preserves_existing_tags(cfg):
    """Calling save_sheet without tags= leaves existing tags intact."""
    row = await _make_sheet(cfg)
    sid = row["id"]
    sheet = await sheets_store.get_sheet(cfg, "u1", sid)
    assert sheet is not None
    snap = sheet["payload"]

    # Set tags first
    await sheets_store.save_sheet(cfg, "u1", None, snap, sheet_id=sid, tags=["keep"])

    # Save again without tags param — tags must be preserved
    sheet2 = await sheets_store.get_sheet(cfg, "u1", sid)
    assert sheet2 is not None
    snap2 = sheet2["payload"]
    await sheets_store.save_sheet(cfg, "u1", None, snap2, sheet_id=sid)

    fetched = await sheets_store.get_sheet(cfg, "u1", sid)
    assert fetched is not None
    assert fetched["tags"] == ["keep"]


# ── sheets_store: cross-user isolation ────────────────────────────────────────


async def test_save_sheet_cross_user_isolation(cfg):
    """u2 calling save_sheet for their own new sheet must NOT affect u1's row.

    save_sheet is scoped by user_id in its UPDATE/SELECT — a u2 write with a
    fresh sheet_id leaves u1's sheet completely untouched.
    """
    u1_row = await _make_sheet(cfg, user_id="u1", name="U1 Sheet")
    u1_sid = u1_row["id"]

    u1_sheet = await sheets_store.get_sheet(cfg, "u1", u1_sid)
    assert u1_sheet is not None
    snap = u1_sheet["payload"]
    u1_original_name = u1_sheet["name"]
    u1_original_updated_at = u1_sheet["updated_at"]

    # u2 creates their own sheet (no sheet_id — fresh insert)
    u2_row = await sheets_store.save_sheet(cfg, "u2", "U2 Sheet", snap)
    u2_sid = u2_row["id"]

    # The two sheet ids must be different (separate rows)
    assert u2_sid != u1_sid

    # u1's sheet must be completely unchanged
    u1_after = await sheets_store.get_sheet(cfg, "u1", u1_sid)
    assert u1_after is not None
    assert u1_after["name"] == u1_original_name
    assert u1_after["updated_at"] == u1_original_updated_at

    # u2 cannot see u1's sheet
    assert await sheets_store.get_sheet(cfg, "u2", u1_sid) is None

    # u1 cannot see u2's sheet
    assert await sheets_store.get_sheet(cfg, "u1", u2_sid) is None


# ── docs_store: DocConflictError mirrors SheetConflictError ───────────────────


async def test_doc_stale_base_raises_conflict(cfg):
    """save_doc with a stale base_updated_at raises DocConflictError."""
    row = await _make_doc(cfg)
    did = row["id"]
    doc = await docs_store.get_doc(cfg, "u1", did)
    assert doc is not None
    snap = doc["payload"]

    # Advance the stored updated_at directly in the DB
    newer_ts = "2026-01-01 12:00:01"
    async with db_session(cfg) as db:
        await db.execute(
            "UPDATE docs SET updated_at = ? WHERE id = ?", (newer_ts, did)
        )
        await db.commit()

    with pytest.raises(docs_store.DocConflictError) as exc_info:
        await docs_store.save_doc(
            cfg, "u1", "Doc", snap,
            doc_id=did,
            base_updated_at=row["updated_at"],  # stale
        )

    err = exc_info.value
    assert hasattr(err, "current")
    assert err.current["id"] == did
    assert "payload" in err.current
    assert err.current["updated_at"] == newer_ts


async def test_doc_save_none_name_preserves_stored(cfg):
    """save_doc(name=None) preserves the existing doc name."""
    row = await _make_doc(cfg, name="My Letter")
    did = row["id"]
    doc = await docs_store.get_doc(cfg, "u1", did)
    assert doc is not None
    snap = doc["payload"]

    result = await docs_store.save_doc(cfg, "u1", None, snap, doc_id=did)
    assert result["name"] == "My Letter"


async def test_doc_tags_round_trip(cfg):
    """Tags work the same way for docs."""
    row = await _make_doc(cfg)
    did = row["id"]
    doc = await docs_store.get_doc(cfg, "u1", did)
    assert doc is not None
    snap = doc["payload"]

    await docs_store.save_doc(cfg, "u1", None, snap, doc_id=did, tags=["draft", "legal"])

    listing = await docs_store.list_docs(cfg, "u1")
    assert listing[0]["tags"] == ["draft", "legal"]

    fetched = await docs_store.get_doc(cfg, "u1", did)
    assert fetched is not None
    assert fetched["tags"] == ["draft", "legal"]


# ── pdf_store: update_pdf_meta ────────────────────────────────────────────────


@pytest.fixture
def minimal_pdf() -> bytes:
    """Minimal PDF-magic bytes for testing store logic (not for parsing)."""
    return b"%PDF-1.4\n%%EOF"


async def test_update_pdf_meta_name_and_tags(cfg, minimal_pdf: bytes):
    """update_pdf_meta sets name and tags without changing the payload bytes."""
    with patch("lazyclaw.pdf.store.ops.page_count", return_value=1):
        row = await pdf_store.save_pdf(cfg, "u1", "original.pdf", minimal_pdf)
    pid = row["id"]

    updated = await pdf_store.update_pdf_meta(
        cfg, "u1", pid, name="renamed.pdf", tags=["invoice", "2026"]
    )

    assert updated is not None
    assert updated["name"] == "renamed.pdf"
    assert updated["tags"] == ["invoice", "2026"]

    # Payload should be unchanged — re-fetch the bytes
    fetched = await pdf_store.get_pdf(cfg, "u1", pid)
    assert fetched is not None
    assert fetched["bytes"] == minimal_pdf


async def test_update_pdf_meta_missing_id_returns_none(cfg):
    """update_pdf_meta returns None for unknown pdf_id."""
    result = await pdf_store.update_pdf_meta(
        cfg, "u1", "nonexistent-id", name="foo.pdf"
    )
    assert result is None


async def test_pdf_tags_default_empty_list(cfg, minimal_pdf: bytes):
    """A newly saved PDF returns tags=[] in list and get."""
    with patch("lazyclaw.pdf.store.ops.page_count", return_value=1):
        row = await pdf_store.save_pdf(cfg, "u1", "test.pdf", minimal_pdf)
    listing = await pdf_store.list_pdfs(cfg, "u1")
    assert listing[0]["tags"] == []
    fetched = await pdf_store.get_pdf(cfg, "u1", row["id"])
    assert fetched is not None
    assert fetched["tags"] == []


# ── sheets_store: foreign-id save raises LookupError ─────────────────────────


async def test_save_sheet_foreign_id_raises_lookup_error(cfg):
    """save_sheet(u2, sheet_id=<u1's id>) raises LookupError, not IntegrityError."""
    u1_row = await _make_sheet(cfg, user_id="u1", name="U1 Sheet")
    u1_sid = u1_row["id"]

    u1_sheet = await sheets_store.get_sheet(cfg, "u1", u1_sid)
    assert u1_sheet is not None
    snap = u1_sheet["payload"]

    with pytest.raises(LookupError, match="sheet not found"):
        await sheets_store.save_sheet(cfg, "u2", "U2 Sheet", snap, sheet_id=u1_sid)


async def test_save_sheet_foreign_id_u1_row_unchanged(cfg):
    """After the failed save attempt, u1's row is completely untouched."""
    u1_row = await _make_sheet(cfg, user_id="u1", name="U1 Sheet")
    u1_sid = u1_row["id"]

    u1_sheet = await sheets_store.get_sheet(cfg, "u1", u1_sid)
    assert u1_sheet is not None
    snap = u1_sheet["payload"]
    original_name = u1_sheet["name"]
    original_updated_at = u1_sheet["updated_at"]

    try:
        await sheets_store.save_sheet(cfg, "u2", "Hijack", snap, sheet_id=u1_sid)
    except LookupError:
        pass

    u1_after = await sheets_store.get_sheet(cfg, "u1", u1_sid)
    assert u1_after is not None
    assert u1_after["name"] == original_name
    assert u1_after["updated_at"] == original_updated_at


# ── docs_store: foreign-id save raises LookupError ───────────────────────────


async def test_save_doc_foreign_id_raises_lookup_error(cfg):
    """save_doc(u2, doc_id=<u1's id>) raises LookupError, not IntegrityError."""
    u1_row = await _make_doc(cfg, user_id="u1", name="U1 Doc")
    u1_did = u1_row["id"]

    u1_doc = await docs_store.get_doc(cfg, "u1", u1_did)
    assert u1_doc is not None
    snap = u1_doc["payload"]

    with pytest.raises(LookupError, match="doc not found"):
        await docs_store.save_doc(cfg, "u2", "U2 Doc", snap, doc_id=u1_did)


async def test_save_doc_foreign_id_u1_row_unchanged(cfg):
    """After the failed save attempt, u1's doc row is completely untouched."""
    u1_row = await _make_doc(cfg, user_id="u1", name="U1 Doc")
    u1_did = u1_row["id"]

    u1_doc = await docs_store.get_doc(cfg, "u1", u1_did)
    assert u1_doc is not None
    snap = u1_doc["payload"]
    original_name = u1_doc["name"]
    original_updated_at = u1_doc["updated_at"]

    try:
        await docs_store.save_doc(cfg, "u2", "Hijack", snap, doc_id=u1_did)
    except LookupError:
        pass

    u1_after = await docs_store.get_doc(cfg, "u1", u1_did)
    assert u1_after is not None
    assert u1_after["name"] == original_name
    assert u1_after["updated_at"] == original_updated_at
