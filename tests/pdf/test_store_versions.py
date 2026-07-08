"""In-place replace + hidden version archive for the PDF store.

The in-editor ✨ specialist edits a PDF *in place* (same id + name, viewer
reloads) instead of forking a suffix-renamed duplicate into the sidebar. The
prior bytes are stashed as a hidden ``version_of`` row so nothing clutters the
list yet the original is recoverable via ``list_pdf_versions`` / ``restore``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.pdf import ops, store
from tests.pdf.conftest import make_text_pdf

pytestmark = pytest.mark.asyncio


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


async def _seed(cfg, text="Original.") -> str:
    row = await store.save_pdf(cfg, "u1", "doc.pdf", make_text_pdf(text))
    return row["id"]


async def test_archive_and_replace_edits_in_place(cfg):
    pid = await _seed(cfg, "Original.")
    new_bytes = make_text_pdf("Edited.")

    row = await store.archive_and_replace(cfg, "u1", pid, new_bytes)

    # Same id + same name → the viewer reloads THIS file, not a new one.
    assert row["id"] == pid
    assert row["name"] == "doc.pdf"
    live = await store.get_pdf(cfg, "u1", pid)
    assert "Edited." in ops.extract_text(live["bytes"])


async def test_replace_keeps_sidebar_at_one_file(cfg):
    pid = await _seed(cfg)
    await store.archive_and_replace(cfg, "u1", pid, make_text_pdf("v2"))
    await store.archive_and_replace(cfg, "u1", pid, make_text_pdf("v3"))

    # Two edits, but the user still sees exactly one live PDF.
    rows = await store.list_pdfs(cfg, "u1")
    assert len(rows) == 1
    assert rows[0]["id"] == pid


async def test_prior_bytes_are_recoverable_as_versions(cfg):
    pid = await _seed(cfg, "Original.")
    await store.archive_and_replace(cfg, "u1", pid, make_text_pdf("Edited."))

    versions = await store.list_pdf_versions(cfg, "u1", pid)
    assert len(versions) == 1
    # The archived version holds the ORIGINAL bytes.
    old = await store.get_pdf(cfg, "u1", versions[0]["id"])
    assert "Original." in ops.extract_text(old["bytes"])


async def test_restore_swaps_live_back_to_the_version(cfg):
    pid = await _seed(cfg, "Original.")
    await store.archive_and_replace(cfg, "u1", pid, make_text_pdf("Edited."))
    versions = await store.list_pdf_versions(cfg, "u1", pid)

    restored = await store.restore_pdf_version(cfg, "u1", versions[0]["id"])

    assert restored is not None and restored["id"] == pid
    live = await store.get_pdf(cfg, "u1", pid)
    assert "Original." in ops.extract_text(live["bytes"])
    # Restore is itself undoable — the "Edited." bytes are now archived.
    texts = [
        ops.extract_text((await store.get_pdf(cfg, "u1", v["id"]))["bytes"])
        for v in await store.list_pdf_versions(cfg, "u1", pid)
    ]
    assert any("Edited." in t for t in texts)


async def test_versions_hidden_from_changes_feed(cfg):
    pid = await _seed(cfg)
    await store.archive_and_replace(cfg, "u1", pid, make_text_pdf("v2"))

    feed = await store.get_pdf_changes(cfg, "u1", since=None)
    ids = {f["id"] for f in feed["files"]}
    # Only the live file syncs to mobile — version rows never leak into the feed.
    assert ids == {pid}


async def test_version_pruning_caps_history(cfg):
    pid = await _seed(cfg)
    for i in range(store.MAX_PDF_VERSIONS + 5):
        await store.archive_and_replace(cfg, "u1", pid, make_text_pdf(f"v{i}"))
    versions = await store.list_pdf_versions(cfg, "u1", pid)
    assert len(versions) <= store.MAX_PDF_VERSIONS


async def test_archive_missing_pdf_raises(cfg):
    with pytest.raises(LookupError):
        await store.archive_and_replace(cfg, "u1", "no-such-id", make_text_pdf())


async def test_restore_is_user_scoped(cfg):
    pid = await _seed(cfg)
    await store.archive_and_replace(cfg, "u1", pid, make_text_pdf("v2"))
    versions = await store.list_pdf_versions(cfg, "u1", pid)
    # u2 cannot restore u1's version.
    assert await store.restore_pdf_version(cfg, "u2", versions[0]["id"]) is None


async def test_restore_wrong_parent_is_rejected_without_mutation(cfg):
    pid_a = await _seed(cfg, "A-original.")
    pid_b = await _seed(cfg, "B-original.")
    await store.archive_and_replace(cfg, "u1", pid_a, make_text_pdf("A-edited."))
    va = (await store.list_pdf_versions(cfg, "u1", pid_a))[0]["id"]

    # Restoring A's version but claiming it belongs to B → refused, no mutation.
    assert (
        await store.restore_pdf_version(
            cfg, "u1", va, expected_parent=pid_b
        )
        is None
    )
    live_b = await store.get_pdf(cfg, "u1", pid_b)
    assert "B-original." in ops.extract_text(live_b["bytes"])
    # A is untouched too (still the edited live bytes, one version).
    assert len(await store.list_pdf_versions(cfg, "u1", pid_a)) == 1
