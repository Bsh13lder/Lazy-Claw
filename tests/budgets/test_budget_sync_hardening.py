# pyright: reportOptionalSubscript=false
"""P1 backend sync hardening for the Budgets domain.

Two regressions this file pins down (RED first, then GREEN):

BUG A — client-minted project id colliding on the UNIQUE ``name_key`` index.
    A mobile-created project (offline uuid) whose name casefolds to an EXISTING
    server project reached the raw INSERT (the name-key merge branch was gated
    ``if existing and not project_id``) and raised ``sqlite3.IntegrityError`` →
    unhandled → HTTP 500. The project and every expense filed against it never
    synced. Fix: resolve ``name_key`` before insert and merge into the existing
    row when a different id already owns that name.

BUG B/D/E — ``budget_entries`` (top-ups) could not cross-sync, retries double-
    added, and deletes left no tombstone. ``budget_entries`` had no
    ``updated_at``/``deleted_at``; ``get_budget_changes`` never returned them;
    ``add_budget_entry`` minted a fresh uuid + re-bumped the budget on every
    call; ``delete_budget_entry`` hard-DELETEd. Fixes: schema + idempotent
    migration for the two columns, client-id idempotency on add, soft-delete,
    and two new ``/changes`` arrays.

Uses the temp-DB isolation pattern (``Config(database_dir=tmp_path)`` + the
session-autouse guard in ``tests/conftest.py``) so the live ``./data`` DB is
never touched.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lazyclaw.budgets import store as budget_store
from lazyclaw.config import Config
from lazyclaw.crypto.key_manager import create_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.gateway.routes.budgets import router as budgets_router

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
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


@pytest.fixture
async def client(tmp_path: Path, monkeypatch):
    c = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    await create_user_dek(c, "u1", "salt-a")

    import lazyclaw.gateway.routes.budgets as routes_mod
    monkeypatch.setattr(routes_mod, "_config", c)

    app = FastAPI()
    app.include_router(budgets_router)
    app.dependency_overrides[get_current_user] = lambda: User(
        id="u1", username="alice", display_name=None,
        encryption_salt="salt-a", role="user",
    )
    try:
        yield TestClient(app)
    finally:
        await close_pool()


async def _make_project(cfg, name="Test Project", budget=100.0) -> dict:
    return await budget_store.create_project(
        cfg, "u1", name, budget=budget, currency="EUR",
    )


# ---------------------------------------------------------------------------
# BUG A — client id + colliding name_key must be idempotent, not 500
# ---------------------------------------------------------------------------


async def test_create_project_client_id_name_collision_no_raise(cfg):
    """A client-minted id whose name matches an existing project must NOT raise
    the UNIQUE(name_key) IntegrityError — it merges into the existing row."""
    web = await budget_store.create_project(
        cfg, "u1", "Nima", budget=500.0, currency="EUR",
    )
    # Same casefolded name, different (client-minted) id — this used to 500.
    mobile = await budget_store.create_project(
        cfg, "u1", "nima", budget=0.0, currency="EUR",
        project_id="mobile-offline-uuid-123",
    )
    # Merged into the pre-existing row (idempotent), not a duplicate insert.
    assert mobile["id"] == web["id"]


async def test_create_project_client_id_name_collision_no_duplicate(cfg):
    """The colliding create must not add a second row for that name_key."""
    await budget_store.create_project(cfg, "u1", "Nima", budget=500.0)
    await budget_store.create_project(
        cfg, "u1", "nima", budget=10.0, project_id="mobile-offline-uuid-456",
    )
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM projects WHERE user_id = 'u1' AND name_key = 'nima'",
        )
        row = await cur.fetchone()
    assert row[0] == 1, "name-key collision must not insert a duplicate project"


async def test_create_project_client_id_collision_isolated_per_user(cfg):
    """A different user's project with the same name must NOT be merged into."""
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u2", "bob", "x", "salt-b"),
        )
        await db.commit()
    await budget_store.create_project(cfg, "u1", "Shared Name", budget=100.0)
    u2_proj = await budget_store.create_project(
        cfg, "u2", "Shared Name", budget=200.0, project_id="u2-client-id",
    )
    # u2's create must use its own client id, not collide with u1's row.
    assert u2_proj["id"] == "u2-client-id"
    assert u2_proj["user_id"] == "u2"


async def test_create_project_route_no_500_on_name_collision(client):
    """Route-level: the offline mobile create must return 200, not 500."""
    r1 = client.post("/api/budgets/projects", json={"name": "Nima", "budget": 500})
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        "/api/budgets/projects",
        json={"id": "mobile-uuid-abc", "name": "nima", "budget": 0},
    )
    assert r2.status_code == 200, r2.text


# ---------------------------------------------------------------------------
# BUG D — add_budget_entry idempotency by client id
# ---------------------------------------------------------------------------


async def test_add_budget_entry_client_id_idempotent_single_row(cfg):
    """Two adds with the same client id → exactly ONE ledger row."""
    project = await _make_project(cfg, budget=100.0)
    await budget_store.add_budget_entry(
        cfg, "u1", project["id"], amount=50.0, source="grant",
        entry_id="client-entry-1",
    )
    await budget_store.add_budget_entry(
        cfg, "u1", project["id"], amount=50.0, source="grant",
        entry_id="client-entry-1",
    )
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM budget_entries WHERE id = ? AND user_id = 'u1'",
            ("client-entry-1",),
        )
        row = await cur.fetchone()
    assert row[0] == 1, "retried add with same client id must not duplicate"


async def test_add_budget_entry_client_id_bumps_budget_once(cfg):
    """The retried add must NOT re-bump the project budget."""
    project = await _make_project(cfg, budget=100.0)
    await budget_store.add_budget_entry(
        cfg, "u1", project["id"], amount=50.0, entry_id="client-entry-2",
    )
    await budget_store.add_budget_entry(
        cfg, "u1", project["id"], amount=50.0, entry_id="client-entry-2",
    )
    fresh = await budget_store.get_project(cfg, "u1", project["id"])
    assert float(fresh["budget"]) == 150.0, "budget must be bumped exactly once"


async def test_add_budget_entry_without_id_still_mints_uuid(cfg):
    """Backward-compat: no client id → fresh uuid, budget bumped normally."""
    project = await _make_project(cfg, budget=100.0)
    entry = await budget_store.add_budget_entry(
        cfg, "u1", project["id"], amount=25.0,
    )
    assert len(entry["id"]) == 36
    fresh = await budget_store.get_project(cfg, "u1", project["id"])
    assert float(fresh["budget"]) == 125.0


# ---------------------------------------------------------------------------
# BUG B — budget_entries surface in the /changes delta feed
# ---------------------------------------------------------------------------


async def test_get_budget_changes_includes_budget_entries_after_since(cfg):
    """A budget_entry created after `since` appears in `budget_entries`."""
    project = await _make_project(cfg, budget=100.0)
    checkpoint = datetime.now(timezone.utc).isoformat()
    await asyncio.sleep(0.02)
    entry = await budget_store.add_budget_entry(
        cfg, "u1", project["id"], amount=40.0, source="topup",
    )
    result = await budget_store.get_budget_changes(cfg, "u1", since=checkpoint)
    assert "budget_entries" in result
    ids = [e["id"] for e in result["budget_entries"]]
    assert entry["id"] in ids


async def test_get_budget_changes_full_sync_has_budget_entry_arrays(cfg):
    """Full sync response always carries both new arrays."""
    project = await _make_project(cfg, budget=100.0)
    await budget_store.add_budget_entry(cfg, "u1", project["id"], amount=10.0)
    result = await budget_store.get_budget_changes(cfg, "u1", since=None)
    assert "budget_entries" in result
    assert "deleted_budget_entries" in result
    assert len(result["budget_entries"]) >= 1


async def test_get_budget_changes_excludes_old_entries_before_since(cfg):
    """A budget_entry created before `since` is not in the live array."""
    project = await _make_project(cfg, budget=100.0)
    old = await budget_store.add_budget_entry(cfg, "u1", project["id"], amount=10.0)
    await asyncio.sleep(0.02)
    checkpoint = datetime.now(timezone.utc).isoformat()
    result = await budget_store.get_budget_changes(cfg, "u1", since=checkpoint)
    ids = [e["id"] for e in result["budget_entries"]]
    assert old["id"] not in ids


# ---------------------------------------------------------------------------
# BUG E — delete_budget_entry soft-deletes + tombstone in /changes
# ---------------------------------------------------------------------------


async def test_delete_budget_entry_soft_deletes(cfg):
    """delete_budget_entry sets deleted_at instead of hard-DELETE."""
    project = await _make_project(cfg, budget=100.0)
    entry = await budget_store.add_budget_entry(cfg, "u1", project["id"], amount=30.0)
    ok = await budget_store.delete_budget_entry(cfg, "u1", entry["id"])
    assert ok is True
    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT deleted_at FROM budget_entries WHERE id = ? AND user_id = 'u1'",
            (entry["id"],),
        )
        row = await cur.fetchone()
    assert row is not None, "row must survive a soft-delete"
    assert row[0] is not None, "deleted_at must be set"


async def test_delete_budget_entry_tombstone_and_absent_from_live_list(cfg):
    """After delete: id shows in deleted_budget_entries and NOT in the live list."""
    project = await _make_project(cfg, budget=100.0)
    entry = await budget_store.add_budget_entry(cfg, "u1", project["id"], amount=30.0)
    checkpoint = datetime.now(timezone.utc).isoformat()
    await asyncio.sleep(0.02)
    await budget_store.delete_budget_entry(cfg, "u1", entry["id"])

    result = await budget_store.get_budget_changes(cfg, "u1", since=checkpoint)
    assert entry["id"] in result["deleted_budget_entries"]

    live = await budget_store.list_budget_entries(cfg, "u1", project["id"])
    assert entry["id"] not in [e["id"] for e in live]


async def test_delete_budget_entry_rolls_back_budget(cfg):
    """Soft-delete still rolls the top-up back out of the project budget."""
    project = await _make_project(cfg, budget=100.0)
    entry = await budget_store.add_budget_entry(cfg, "u1", project["id"], amount=40.0)
    assert float((await budget_store.get_project(cfg, "u1", project["id"]))["budget"]) == 140.0
    await budget_store.delete_budget_entry(cfg, "u1", entry["id"])
    fresh = await budget_store.get_project(cfg, "u1", project["id"])
    assert float(fresh["budget"]) == 100.0


# ---------------------------------------------------------------------------
# update_budget_entry bumps updated_at
# ---------------------------------------------------------------------------


async def test_update_budget_entry_bumps_updated_at(cfg):
    """Editing a ledger entry advances updated_at so it re-syncs."""
    project = await _make_project(cfg, budget=100.0)
    entry = await budget_store.add_budget_entry(cfg, "u1", project["id"], amount=40.0)

    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT updated_at FROM budget_entries WHERE id = ?", (entry["id"],),
        )
        before_row = await cur.fetchone()
        assert before_row is not None
        before = before_row[0]

    await asyncio.sleep(0.02)
    await budget_store.update_budget_entry(cfg, "u1", entry["id"], amount=60.0)

    async with db_session(cfg) as db:
        cur = await db.execute(
            "SELECT updated_at FROM budget_entries WHERE id = ?", (entry["id"],),
        )
        after_row = await cur.fetchone()
        assert after_row is not None
        after = after_row[0]

    assert after is not None
    assert after > before, "updated_at must advance on edit"


# ---------------------------------------------------------------------------
# Migration — idempotency + backfill
# ---------------------------------------------------------------------------


async def test_budget_entries_migration_columns_exist(cfg):
    """The migration adds updated_at + deleted_at to budget_entries."""
    async with db_session(cfg) as db:
        cur = await db.execute("PRAGMA table_info(budget_entries)")
        cols = [r[1] for r in await cur.fetchall()]
    assert "updated_at" in cols
    assert "deleted_at" in cols


async def test_init_db_is_idempotent(cfg):
    """Running init_db twice on the same DB must not error (idempotent migration)."""
    await init_db(cfg)  # second run
    async with db_session(cfg) as db:
        cur = await db.execute("PRAGMA table_info(budget_entries)")
        cols = [r[1] for r in await cur.fetchall()]
    # Columns present exactly once (no duplicate ALTER damage).
    assert cols.count("updated_at") == 1
    assert cols.count("deleted_at") == 1


async def test_migration_backfills_updated_at(tmp_path: Path):
    """A pre-migration budget_entries row (no updated_at) is backfilled to
    created_at when init_db runs the migration + backfill pass."""
    c = Config(database_dir=tmp_path, server_secret="test-secret-32-bytes-long-yo")
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "testuser", "x", "salt-test"),
        )
        # Simulate a legacy row inserted before the column existed: explicit
        # NULL updated_at with a known created_at.
        await db.execute(
            "INSERT INTO budget_entries "
            "(id, user_id, project_id, amount, currency, source, kind, "
            "created_at, updated_at, deleted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
            ("legacy-1", "u1", "p1", 10.0, "EUR", None, "credit",
             "2026-01-01T00:00:00+00:00"),
        )
        await db.commit()

    # Re-run init_db → backfill pass fires.
    await init_db(c)

    async with db_session(c) as db:
        cur = await db.execute(
            "SELECT created_at, updated_at FROM budget_entries WHERE id = 'legacy-1'",
        )
        legacy_row = await cur.fetchone()
        assert legacy_row is not None
        created_at, updated_at = legacy_row
    await close_pool()
    assert updated_at == created_at, "legacy row updated_at must backfill to created_at"
