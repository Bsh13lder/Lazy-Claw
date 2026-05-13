"""Race-safety regression for journal.ensure_today_journal() and
journal.append_journal() under concurrent calls.

The DB audit on 2026-05-13 found duplicate `journal/<date>`-tagged
stubs per day — three rows for 2026-05-13, two for 2026-05-12, each
empty stub sitting next to the real journal with all the bullets.
Root cause: read-then-write race between concurrent coroutines, both
seeing 0 rows on `get_journal()`, both calling `save_note()`. Fix
adds per-(user, date) asyncio.Lock around the get-then-save window.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.lazybrain import journal, store


@pytest.fixture(autouse=True)
def _reset_journal_locks():
    """Each pytest-asyncio test gets its own event loop, but the
    module-level _JOURNAL_LOCKS dict persists across tests. Locks
    bound to a stale loop blow up with `Lock is bound to a different
    event loop`. Reset between tests; the production server runs one
    long-lived loop so this fixture is test-only hygiene.
    """
    journal._JOURNAL_LOCKS.clear()
    yield
    journal._JOURNAL_LOCKS.clear()


@pytest.fixture
async def tmp_config(tmp_path: Path):
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u-race", "racer", "x", "salt-race-test"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


@pytest.mark.asyncio
async def test_concurrent_ensure_today_journal_no_dupes(
    tmp_config: Config,
) -> None:
    """Ten coroutines calling ensure_today_journal in parallel must
    yield exactly one journal row, not ten."""
    results = await asyncio.gather(*[
        journal.ensure_today_journal(tmp_config, "u-race")
        for _ in range(10)
    ])

    # All callers got the same note id.
    ids = {r["id"] for r in results}
    assert len(ids) == 1

    # DB has exactly one row tagged journal/<today>.
    async with db_session(tmp_config) as db:
        rows = await db.execute(
            "SELECT COUNT(*) FROM notes "
            "WHERE user_id = ? AND tags LIKE '%journal/%'",
            ("u-race",),
        )
        (count,) = await (await db.execute(
            "SELECT COUNT(*) FROM notes "
            "WHERE user_id = ? AND tags LIKE '%journal/%'",
            ("u-race",),
        )).fetchone()
    assert count == 1


@pytest.mark.asyncio
async def test_concurrent_append_journal_no_dupes(
    tmp_config: Config,
) -> None:
    """Five concurrent append_journal calls (no pre-existing journal)
    create exactly one journal row, and all five bullets land inside it.
    """
    await asyncio.gather(*[
        journal.append_journal(
            tmp_config, "u-race",
            f"[[Task: bullet-{i}]] — done",
        )
        for i in range(5)
    ])

    async with db_session(tmp_config) as db:
        rows = await db.execute(
            "SELECT id FROM notes "
            "WHERE user_id = ? AND tags LIKE '%journal/%'",
            ("u-race",),
        )
        rows = await rows.fetchall()
    assert len(rows) == 1
    journal_id = rows[0][0]

    note = await store.get_note(tmp_config, "u-race", journal_id)
    body = note["content"]
    # All five bullets present.
    for i in range(5):
        assert f"bullet-{i}" in body, f"bullet-{i} missing from {body!r}"


@pytest.mark.asyncio
async def test_append_then_ensure_returns_same_row(
    tmp_config: Config,
) -> None:
    """ensure_today_journal must return the journal that append_journal
    already created — never construct a second stub."""
    appended = await journal.append_journal(
        tmp_config, "u-race", "[[Task: first]] — done",
    )
    ensured = await journal.ensure_today_journal(tmp_config, "u-race")
    assert appended["id"] == ensured["id"]


@pytest.mark.asyncio
async def test_locks_are_per_user_per_date(
    tmp_config: Config,
) -> None:
    """Lock scope is (user_id, date) — different users / different dates
    must not serialize through the same lock (would tank throughput)."""
    lock_a = journal._journal_lock("u-race", "2026-05-13")
    lock_b = journal._journal_lock("u-race", "2026-05-13")
    lock_c = journal._journal_lock("u-race", "2026-05-14")
    lock_d = journal._journal_lock("other-user", "2026-05-13")
    assert lock_a is lock_b
    assert lock_a is not lock_c
    assert lock_a is not lock_d
