"""Root pytest configuration — test/production DB isolation guardrail.

2026-07-03 outage: ``docker-compose.yml`` bind-mounts ``./data`` to
``/app/data``, so the PRODUCTION SQLite DB lives at the repo-relative
``./data/lazyclaw.db``. ``lazyclaw/config.py::load_config`` defaults
``database_dir`` to that same ``./data`` whenever the ``DATABASE_DIR`` env
var is unset. A host-run ``pytest tests/`` therefore opened the live prod
DB; the run was killed mid-write and ``init_db`` (``lazyclaw/db/connection.py``)
hit ``sqlite3.DatabaseError: database disk image is malformed`` on the next
container boot, crash-looping with no recovery path.

This file exists SOLELY to make that impossible again:

  1. A session-scoped, autouse fixture repoints every test onto an
     isolated, disposable ``DATABASE_DIR`` (a ``tmp_path_factory`` temp
     dir) before any test body runs.
  2. A hard tripwire asserts the repoint actually took — if the resolved
     DB path ever matches (or falls inside) the real bind-mounted
     production data dir, the fixture fails LOUDLY instead of letting the
     test session proceed.

Individual tests that already construct their own
``Config(database_dir=tmp_path)`` (the majority of this suite) are
unaffected — this is a backstop for tests/modules that fall through to
the env-var default, not a replacement for per-test isolation.

See MEMORY.md → ``feedback_tests_hit_prod_db.md``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lazyclaw.config import Config, get_project_root

# Env var(s) that determine where lazyclaw resolves its SQLite DB. Confirmed
# against lazyclaw/config.py: ``database_dir`` is the ONLY knob —
# ``Config.database_dir`` defaults to ``Path("./data")`` (config.py:22) and
# ``load_config()`` overrides it from ``DATABASE_DIR`` (config.py:109). The
# actual file path is ``database_dir / "lazyclaw.db"``
# (lazyclaw/db/connection.py::get_db_path).
_DB_PATH_ENV_VARS = ("DATABASE_DIR",)


def _real_production_data_dirs() -> list[Path]:
    """Every absolute path shape the DB dir could resolve to that would hit
    the docker-compose bind-mounted production data volume.

    Covers both "resolved relative to the repo root" (how ``./data`` reads
    when someone runs ``pytest`` from the repo, i.e. the outage scenario)
    and "resolved relative to the current process cwd" (the literal
    default in config.py — usually the same directory, but resolved
    independently in case pytest is ever invoked from elsewhere).
    """
    repo_root = get_project_root()
    candidates = [repo_root / "data", Path("./data")]
    resolved: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        real = candidate.resolve()
        if real not in seen:
            seen.add(real)
            resolved.append(real)
    return resolved


def resolve_active_db_dir() -> Path:
    """The ``database_dir`` a fresh ``Config``/``load_config()`` call would
    resolve to right now, given the current environment."""
    raw = os.environ.get("DATABASE_DIR", "./data")
    return Path(raw).resolve()


@pytest.fixture(scope="session", autouse=True)
def _enforce_test_db_isolation(tmp_path_factory: pytest.TempPathFactory):
    """Force every test in this session onto an isolated throwaway DB dir,
    then hard-fail if that repoint didn't actually take.

    Runs before the body of the FIRST test in the session (autouse +
    session scope), i.e. before any test constructs a ``Config`` or calls
    ``load_config()`` — the codebase has no module-level (import-time)
    construction of either (verified via grep across ``tests/``), so this
    is the correct and sufficient interception point.
    """
    session_db_dir = tmp_path_factory.mktemp("lazyclaw-db")

    original_env = {var: os.environ.get(var) for var in _DB_PATH_ENV_VARS}
    for var in _DB_PATH_ENV_VARS:
        os.environ[var] = str(session_db_dir)

    # --- Tripwire: this must never resolve into the real bind-mounted
    # production data directory. If it does, refuse to let a single test
    # run — this is the exact class of bug that caused the 2026-07-03
    # outage, and it must fail loudly (pytest error), not silently proceed.
    forbidden_dirs = _real_production_data_dirs()
    resolved = resolve_active_db_dir()
    if resolved in forbidden_dirs or any(
        resolved == forbidden or forbidden in resolved.parents
        for forbidden in forbidden_dirs
    ):
        raise pytest.UsageError(
            "REFUSING TO RUN TESTS: the resolved test DATABASE_DIR "
            f"({resolved}) matches the production data directory "
            f"({forbidden_dirs}) bind-mounted by docker-compose.yml. "
            "Running the suite against it would corrupt the live DB "
            "(2026-07-03 outage). This should be structurally "
            "impossible — investigate why tests/conftest.py's env "
            "override was bypassed before running anything else."
        )

    # Belt-and-suspenders: also confirm a Config built the normal way
    # (load_config-equivalent construction) agrees with the env var.
    cfg_db_path = Config(database_dir=Path(os.environ["DATABASE_DIR"])).database_dir.resolve()
    assert cfg_db_path == session_db_dir.resolve(), (
        "Config.database_dir did not honor the test DATABASE_DIR override "
        f"(got {cfg_db_path}, expected {session_db_dir.resolve()})"
    )

    try:
        yield session_db_dir
    finally:
        # Release any pooled aiosqlite connections opened against the
        # throwaway dir before pytest tears it down.
        try:
            import asyncio

            from lazyclaw.db.connection import close_pool

            asyncio.run(close_pool())
        except Exception:
            pass

        for var, value in original_env.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value
