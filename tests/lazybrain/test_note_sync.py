"""Offline-sync primitives for the LazyBrain Notes domain.

Covers three requirements (mirrors tests/tasks/test_task_sync.py):
1. Client-supplied id on create is honoured; duplicate create is idempotent.
2. Soft-delete (deleted_at): hides from list/get/search but surfaces in /changes.
3. GET /api/lazybrain/notes/changes?since=<iso>: delta filtering including tombstones.

Note: updated_at already exists on the notes table and is already bumped on
update_note() — that's out of scope for this test file.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import store as note_store

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "testuser", "x", "salt-test"),
        )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


# ---------------------------------------------------------------------------
# 1. Client-supplied id on create (idempotent replay)
# ---------------------------------------------------------------------------

async def test_create_note_with_client_id(cfg):
    """save_note with note_id= uses the client-supplied id."""
    client_id = "client-minted-note-abc123"
    note = await note_store.save_note(
        cfg, "u1", content="hello world", note_id=client_id
    )
    assert note["id"] == client_id


async def test_create_note_with_client_id_idempotent(cfg):
    """Second save_note with the same id returns the existing note (no duplicate)."""
    client_id = "idem-note-xyz789"
    first = await note_store.save_note(
        cfg, "u1", content="idempotent content", note_id=client_id
    )
    second = await note_store.save_note(
        cfg, "u1", content="idempotent content", note_id=client_id
    )

    assert first["id"] == client_id
    assert second["id"] == client_id, "duplicate client-id must return the same note"

    # Only one row in DB.
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM notes WHERE id = ? AND user_id = 'u1'",
            (client_id,),
        )
        row = await cur.fetchone()
    assert row[0] == 1, "idempotent create must not insert duplicate rows"


async def test_create_note_without_client_id_generates_uuid(cfg):
    """Without note_id=, a fresh UUID is generated."""
    note = await note_store.save_note(cfg, "u1", content="auto-id note")
    assert len(note["id"]) == 36, "auto-generated id should be a UUID"


# ---------------------------------------------------------------------------
# 2. Soft-delete: hides from normal reads, visible in changes
# ---------------------------------------------------------------------------

async def test_delete_note_sets_deleted_at(cfg):
    """delete_note sets deleted_at instead of removing the row."""
    note = await note_store.save_note(cfg, "u1", content="soft delete me")
    ok = await note_store.delete_note(cfg, "u1", note["id"])
    assert ok is True

    # Row still exists in DB.
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT deleted_at FROM notes WHERE id = ? AND user_id = 'u1'",
            (note["id"],),
        )
        row = await cur.fetchone()
    assert row is not None, "row must still exist after soft-delete"
    assert row[0] is not None, "deleted_at must be set after delete_note"


async def test_get_note_returns_none_after_soft_delete(cfg):
    """get_note hides soft-deleted notes."""
    note = await note_store.save_note(cfg, "u1", content="hidden note")
    await note_store.delete_note(cfg, "u1", note["id"])
    result = await note_store.get_note(cfg, "u1", note["id"])
    assert result is None, "get_note must not return soft-deleted notes"


async def test_list_notes_excludes_soft_deleted(cfg):
    """list_notes excludes soft-deleted notes."""
    n_keep = await note_store.save_note(cfg, "u1", content="keep me")
    n_del = await note_store.save_note(cfg, "u1", content="will be deleted")
    await note_store.delete_note(cfg, "u1", n_del["id"])

    notes = await note_store.list_notes(cfg, "u1")
    ids = [n["id"] for n in notes]
    assert n_keep["id"] in ids, "non-deleted note must appear in list"
    assert n_del["id"] not in ids, "soft-deleted note must not appear in list"


async def test_search_notes_excludes_soft_deleted(cfg):
    """search_notes excludes soft-deleted notes."""
    n_keep = await note_store.save_note(cfg, "u1", content="searchable unique phrase alive")
    n_del = await note_store.save_note(cfg, "u1", content="searchable unique phrase deleted")
    await note_store.delete_note(cfg, "u1", n_del["id"])

    results = await note_store.search_notes(cfg, "u1", "searchable unique phrase")
    ids = [n["id"] for n in results]
    assert n_keep["id"] in ids, "non-deleted note must appear in search"
    assert n_del["id"] not in ids, "soft-deleted note must not appear in search"


async def test_delete_note_returns_false_for_missing_note(cfg):
    """delete_note returns False if the note doesn't exist (no crash)."""
    result = await note_store.delete_note(cfg, "u1", "nonexistent-id")
    assert result is False


async def test_double_soft_delete_is_idempotent(cfg):
    """Calling delete_note twice on the same note returns False the second time."""
    note = await note_store.save_note(cfg, "u1", content="delete me twice")
    first = await note_store.delete_note(cfg, "u1", note["id"])
    second = await note_store.delete_note(cfg, "u1", note["id"])
    assert first is True
    assert second is False, "second soft-delete must return False (already deleted)"


# ---------------------------------------------------------------------------
# 3. GET /api/lazybrain/notes/changes?since=<iso> — delta feed
# ---------------------------------------------------------------------------

async def test_get_changes_returns_all_when_no_since(cfg):
    """Without ?since=, all notes (including tombstones) are returned."""
    n1 = await note_store.save_note(cfg, "u1", content="note one")
    n2 = await note_store.save_note(cfg, "u1", content="note two")
    await note_store.delete_note(cfg, "u1", n2["id"])

    result = await note_store.get_note_changes(cfg, "u1", since=None)
    note_ids = [n["id"] for n in result["notes"]]
    assert n1["id"] in note_ids
    assert n2["id"] in result["deleted"], "soft-deleted id must appear in deleted list"
    assert "now" in result


async def test_get_changes_filters_by_since(cfg):
    """Only notes updated after `since` are returned."""
    import asyncio

    n_before = await note_store.save_note(cfg, "u1", content="before")
    checkpoint = datetime.now(timezone.utc).isoformat()
    await asyncio.sleep(0.02)
    n_after = await note_store.save_note(cfg, "u1", content="after")

    result = await note_store.get_note_changes(cfg, "u1", since=checkpoint)
    note_ids = [n["id"] for n in result["notes"]]

    assert n_after["id"] in note_ids, "note created after since must appear"
    assert n_before["id"] not in note_ids, "note created before since must be excluded"


async def test_get_changes_includes_deleted_after_since(cfg):
    """Soft-deletes that happened after `since` appear in the deleted list."""
    import asyncio

    n = await note_store.save_note(cfg, "u1", content="to be deleted later")
    checkpoint = datetime.now(timezone.utc).isoformat()
    await asyncio.sleep(0.02)
    await note_store.delete_note(cfg, "u1", n["id"])

    result = await note_store.get_note_changes(cfg, "u1", since=checkpoint)
    assert n["id"] in result["deleted"], (
        "note soft-deleted after since must appear in deleted list"
    )


async def test_get_changes_excludes_old_deletes_before_since(cfg):
    """Soft-deletes that happened before `since` are not in the deleted list."""
    import asyncio

    n = await note_store.save_note(cfg, "u1", content="old delete")
    await note_store.delete_note(cfg, "u1", n["id"])
    await asyncio.sleep(0.02)
    checkpoint = datetime.now(timezone.utc).isoformat()

    result = await note_store.get_note_changes(cfg, "u1", since=checkpoint)
    assert n["id"] not in result["deleted"], (
        "soft-delete that predates since must NOT appear in deleted list"
    )


async def test_get_changes_now_field_is_valid_iso(cfg):
    """The `now` field in the response is a valid ISO datetime."""
    result = await note_store.get_note_changes(cfg, "u1", since=None)
    ts = result["now"]
    dt = datetime.fromisoformat(ts)
    assert dt is not None


async def test_get_changes_live_notes_are_decrypted(cfg):
    """Notes in the `notes` list from get_note_changes are fully decrypted."""
    n = await note_store.save_note(cfg, "u1", content="decrypted check", title="My Title")
    result = await note_store.get_note_changes(cfg, "u1", since=None)

    matching = [x for x in result["notes"] if x["id"] == n["id"]]
    assert matching, "created note must appear in changes"
    note = matching[0]
    assert note["title"] == "My Title", "title must be decrypted"
    assert note["content"] == "decrypted check", "content must be decrypted"


async def test_deleted_at_column_exists_in_db(cfg):
    """The deleted_at column must exist on the notes table after init_db."""
    async with db_session(cfg) as db:
        row = await db.execute("PRAGMA table_info(notes)")
        columns = [r[1] for r in await row.fetchall()]
    assert "deleted_at" in columns, "deleted_at column must be added by migration"


# ---------------------------------------------------------------------------
# 4. Route ordering: /notes/changes must not be shadowed by /notes/{note_id}
# ---------------------------------------------------------------------------

async def test_notes_changes_route_declared_before_note_id_route():
    """FastAPI matches routes in declaration order. The static
    ``/notes/changes`` route MUST be declared before the parametrised
    ``/notes/{note_id}`` route — otherwise ``GET /notes/changes`` binds
    ``note_id="changes"`` and 404s, silently breaking mobile offline sync
    (2026-07-03 incident). Import-only regression guard (async only to
    satisfy the module-level asyncio mark).
    """
    from lazyclaw.gateway.routes.lazybrain import router

    paths = [
        getattr(r, "path", "") for r in router.routes
    ]
    changes_path = "/api/lazybrain/notes/changes"
    note_id_path = "/api/lazybrain/notes/{note_id}"
    assert changes_path in paths, "the /notes/changes route must be registered"
    assert note_id_path in paths, "the /notes/{note_id} route must be registered"
    assert paths.index(changes_path) < paths.index(note_id_path), (
        "/notes/changes must be declared BEFORE /notes/{note_id} or FastAPI "
        "will match 'changes' as a note_id and 404 the delta-sync feed"
    )
