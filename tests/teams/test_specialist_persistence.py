"""ADR-0005 follow-up: ``include_scraper`` must survive the DB round-trip.

The /api/specialists routes accepted ``include_scraper`` on POST/PUT and
passed it into ``SpecialistConfig``, but ``save_specialist`` never wrote
it and ``load_specialists`` never read it — custom specialists silently
lost scraper access on every restart.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lazyclaw.config import Config
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.teams.specialist import (
    SpecialistConfig,
    load_specialists,
    save_specialist,
)

_USER = "user-spec-persist"


@pytest.fixture
async def tmp_config(tmp_path: Path):
    """Fresh DB + a registered user with a derivable DEK."""
    cfg = Config(database_dir=tmp_path)
    await init_db(cfg)
    async with db_session(cfg) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            (_USER, "alice", "x", "salt-spec-persist"),
        )
        await db.commit()
    try:
        yield cfg
    finally:
        await close_pool()


def _spec(include_scraper: bool) -> SpecialistConfig:
    return SpecialistConfig(
        name="my_custom_spec",
        display_name="My Custom Spec",
        system_prompt="Do custom things.",
        allowed_skills=("web_search",),
        include_scraper=include_scraper,
    )


async def _load_mine(cfg: Config) -> SpecialistConfig:
    loaded = await load_specialists(cfg, _USER)
    return next(s for s in loaded if s.name == "my_custom_spec")


@pytest.mark.asyncio
async def test_include_scraper_true_survives_round_trip(tmp_config):
    await save_specialist(tmp_config, _USER, _spec(include_scraper=True))
    assert (await _load_mine(tmp_config)).include_scraper is True


@pytest.mark.asyncio
async def test_include_scraper_update_persists(tmp_config):
    await save_specialist(tmp_config, _USER, _spec(include_scraper=True))
    # Upsert path: same name, scraper toggled off.
    await save_specialist(tmp_config, _USER, _spec(include_scraper=False))
    assert (await _load_mine(tmp_config)).include_scraper is False


@pytest.mark.asyncio
async def test_update_does_not_duplicate_rows(tmp_config):
    """Latent upsert bug: the existing-row lookup compared the ENCRYPTED
    name, but AES-GCM is nonce-randomized so it never matched — every
    'update' inserted a duplicate row. Must match like delete_specialist:
    decrypt-and-compare."""
    await save_specialist(tmp_config, _USER, _spec(include_scraper=True))
    await save_specialist(tmp_config, _USER, _spec(include_scraper=False))
    async with db_session(tmp_config) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM specialists WHERE user_id = ?", (_USER,)
        )
        (count,) = await cur.fetchone()
    assert count == 1


@pytest.mark.asyncio
async def test_include_scraper_defaults_false(tmp_config):
    await save_specialist(tmp_config, _USER, _spec(include_scraper=False))
    assert (await _load_mine(tmp_config)).include_scraper is False
