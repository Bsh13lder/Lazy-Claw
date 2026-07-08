"""Regression coverage for the 2026-07-03 prod-DB-corruption outage.

Two independent guardrails are exercised here:

1. Test/prod isolation (``tests/conftest.py``): every test session must be
   repointed onto a throwaway ``DATABASE_DIR`` before any test runs, never
   the real bind-mounted ``./data``.
2. Startup integrity guard (``lazyclaw/db/connection.py::init_db``): an
   EXISTING, malformed DB file must fail loudly with a clear, actionable
   error and get backed up (not deleted/auto-repaired) instead of letting
   a raw ``sqlite3.DatabaseError`` surface mid-crash-loop. A path with no
   existing file (fresh install) must still init cleanly.
"""

from __future__ import annotations

import os
import random
import sqlite3
from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import (
    DatabaseCorruptionError,
    close_pool,
    get_db_path,
    init_db,
)
from tests.conftest import _real_production_data_dirs, resolve_active_db_dir


# ---------------------------------------------------------------------------
# 1. conftest self-check — the tripwire this outage needed and didn't have.
# ---------------------------------------------------------------------------


def test_tripwire_detects_the_real_prod_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit-test the detection logic the session fixture's tripwire relies
    on: pointing DATABASE_DIR at the real repo ``./data`` dir must be
    recognized as forbidden. (The fixture itself already ran successfully
    for THIS session — by definition it can't also demonstrate its own
    failure path without spawning a nested pytest process — so this pins
    the pure detection function it calls instead.)"""
    repo_data_dir = _real_production_data_dirs()[0]
    monkeypatch.setenv("DATABASE_DIR", str(repo_data_dir))

    resolved = resolve_active_db_dir()
    forbidden = _real_production_data_dirs()

    assert resolved in forbidden or any(
        resolved == f or f in resolved.parents for f in forbidden
    ), "tripwire logic failed to flag the real production data dir"


def test_tripwire_allows_the_session_tmp_dir() -> None:
    """Negative case: the dir the session fixture actually chose must NOT
    be flagged as forbidden (else every test run would self-destruct)."""
    resolved = resolve_active_db_dir()
    forbidden = _real_production_data_dirs()
    assert resolved not in forbidden
    assert not any(resolved == f or f in resolved.parents for f in forbidden)


def test_active_db_dir_is_not_the_repo_data_dir() -> None:
    """The DATABASE_DIR the test session is actually using must be a
    disposable tmp dir, never the repo-relative ``./data`` that
    docker-compose bind-mounts to the live production DB."""
    active = resolve_active_db_dir()
    repo_data_dir = (Path(__file__).resolve().parent.parent / "data").resolve()

    assert active != repo_data_dir, (
        f"DATABASE_DIR resolved to the real repo data dir ({active}) — "
        "this is exactly the 2026-07-03 outage condition."
    )
    assert "lazyclaw-db" in str(active), (
        f"expected the conftest-managed pytest tmp dir, got {active}"
    )
    # Sanity: DATABASE_DIR env var itself must actually be set (not merely
    # falling back to config.py's own "./data" default and getting lucky).
    assert os.environ.get("DATABASE_DIR") == str(active)


def test_config_honors_the_test_db_override(tmp_path: Path) -> None:
    """A freshly constructed Config must never silently default back to
    the repo's ./data — DATABASE_DIR (session fixture) must win."""
    cfg = Config(database_dir=Path(os.environ["DATABASE_DIR"]))
    repo_data_dir = (Path(__file__).resolve().parent.parent / "data").resolve()
    assert cfg.database_dir.resolve() != repo_data_dir


# ---------------------------------------------------------------------------
# 2. Startup integrity guard
# ---------------------------------------------------------------------------


def _write_malformed_db(path: Path) -> None:
    """Build a real sqlite DB with data, then corrupt bytes mid-file so it
    fails PRAGMA quick_check / raises 'database disk image is malformed' —
    matching the exact failure mode from the outage, not just a garbage
    file that sqlite would reject outright as "not a database"."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE foo (id INTEGER PRIMARY KEY, val TEXT)")
    for i in range(500):
        conn.execute("INSERT INTO foo (val) VALUES (?)", (f"row-{i}" * 20,))
    conn.commit()
    conn.close()

    size = path.stat().st_size
    rng = random.Random(1234)
    with open(path, "r+b") as fh:
        fh.seek(size // 2)
        fh.write(bytes(rng.randint(0, 255) for _ in range(400)))


@pytest.mark.asyncio
async def test_init_db_quarantines_a_malformed_existing_db(tmp_path: Path) -> None:
    cfg = Config(database_dir=tmp_path)
    db_path = get_db_path(cfg)
    tmp_path.mkdir(parents=True, exist_ok=True)
    _write_malformed_db(db_path)

    with pytest.raises(DatabaseCorruptionError) as excinfo:
        await init_db(cfg)

    message = str(excinfo.value)
    assert str(db_path) in message
    assert ".recover" in message or "recover" in message.lower()

    # Corrupt original left in place, untouched...
    assert db_path.exists()
    # ...and a timestamped backup copy was written alongside it.
    backups = list(tmp_path.glob("lazyclaw.db.corrupt-*.bak"))
    assert len(backups) == 1, f"expected exactly one backup, found {backups}"
    assert backups[0].stat().st_size == db_path.stat().st_size

    await close_pool()


@pytest.mark.asyncio
async def test_init_db_still_initializes_a_fresh_empty_path(tmp_path: Path) -> None:
    """Unaffected-behavior guarantee: a brand new install (no db file yet)
    must still init cleanly — the corruption guard is a no-op here."""
    cfg = Config(database_dir=tmp_path)
    db_path = get_db_path(cfg)
    assert not db_path.exists()

    await init_db(cfg)

    assert db_path.exists()
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("PRAGMA quick_check")
        assert cursor.fetchone()[0] == "ok"
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "users" in tables
    finally:
        conn.close()

    await close_pool()
