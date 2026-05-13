"""--force-reimport flag tests for ``cli_migrate_lazybrain``.

The idempotency guard at ``_already_imported`` early-returns when ANY
note carries ``#imported/<source>``. Once an import has run, the only
way to re-sync after a schema fix is to clear those notes first. The
``--force-reimport`` flag does exactly that — deletes existing
``#imported/<source>`` rows for the user before re-importing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw import cli_migrate_lazybrain as mod
from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import store


@pytest.fixture
async def tmp_config(tmp_path: Path):
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u-force", "force", "x", "salt-force-test"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


@pytest.mark.asyncio
async def test_purge_imported_deletes_only_tagged_rows(tmp_config: Config) -> None:
    """``_purge_imported`` deletes notes carrying the source tag and
    leaves everything else alone."""
    keep = await store.save_note(
        tmp_config, "u-force",
        content="Not imported — user-authored.",
        title="Real note",
        tags=["owner/user"],
    )
    drop = await store.save_note(
        tmp_config, "u-force",
        content="Imported from personal_memory.",
        title="Imported note",
        tags=["imported/personal", "kind/fact", "owner/user"],
    )

    n = await mod._purge_imported(
        tmp_config, "u-force", "imported/personal", dry_run=False,
    )
    assert n == 1

    async with db_session(tmp_config) as db:
        rows = await db.execute(
            "SELECT id FROM notes WHERE user_id = ?", ("u-force",),
        )
        remaining = {r[0] for r in await rows.fetchall()}
    assert keep["id"] in remaining
    assert drop["id"] not in remaining


@pytest.mark.asyncio
async def test_purge_imported_dry_run_does_not_write(tmp_config: Config) -> None:
    """Dry-run reports the count without deleting."""
    await store.save_note(
        tmp_config, "u-force",
        content="Imported row.",
        title="Imported note",
        tags=["imported/daily", "owner/user"],
    )
    n = await mod._purge_imported(
        tmp_config, "u-force", "imported/daily", dry_run=True,
    )
    assert n == 1

    async with db_session(tmp_config) as db:
        rows = await db.execute(
            "SELECT COUNT(*) FROM notes WHERE user_id = ?", ("u-force",),
        )
        (count,) = await (await db.execute(
            "SELECT COUNT(*) FROM notes WHERE user_id = ?", ("u-force",),
        )).fetchone()
    assert count == 1


@pytest.mark.asyncio
async def test_force_reimport_skips_already_imported_guard(
    tmp_config: Config, monkeypatch
) -> None:
    """Second migration with ``force_reimport=True`` deletes the old
    imported rows and runs the import body — proven by checking that
    a stub re-imported source row produces a fresh note."""
    # Seed a "leftover" import note that would normally block re-import.
    await store.save_note(
        tmp_config, "u-force",
        content="leftover from a previous import pass",
        title="learned_preference: legacy row",
        tags=["imported/personal", "kind/fact", "owner/user"],
        importance=5,
    )

    # Stub out the actual source-table read so we don't need to encrypt
    # a real personal_memory row for this test. ``migrate_personal_memory``
    # calls ``decrypt_field`` on stored rows — easier to monkey-patch.
    async with db_session(tmp_config) as db:
        await db.execute(
            "INSERT INTO personal_memory (id, user_id, memory_type, content, importance) "
            "VALUES (?, ?, ?, ?, ?)",
            ("src-1", "u-force", "fact", "enc:fake", 5),
        )
        await db.commit()

    monkeypatch.setattr(
        "lazyclaw.cli_migrate_lazybrain.decrypt_field",
        lambda *a, **kw: "post-fix content",
    )

    # Without force: returns empty (early-return on _already_imported).
    result_noop = await mod.migrate_personal_memory(
        tmp_config, "u-force", dry_run=False, force_reimport=False,
    )
    assert result_noop == {}

    # With force: leftover note is purged + the source row gets a
    # fresh mirror.
    result_force = await mod.migrate_personal_memory(
        tmp_config, "u-force", dry_run=False, force_reimport=True,
    )
    assert "src-1" in result_force

    # Exactly one imported/personal note exists now — the re-import,
    # not the leftover.
    async with db_session(tmp_config) as db:
        rows = await db.execute(
            "SELECT content FROM notes WHERE user_id = ? "
            "AND tags LIKE '%imported/personal%'",
            ("u-force",),
        )
        contents = await rows.fetchall()
    assert len(contents) == 1
