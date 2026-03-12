from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from lazyclaw.config import Config


def get_db_path(config: Config) -> Path:
    return config.database_dir / "lazyclaw.db"


async def init_db(config: Config) -> None:
    config.database_dir.mkdir(parents=True, exist_ok=True)
    schema_path = Path(__file__).parent / "schema.sql"
    schema_sql = schema_path.read_text()

    db_path = get_db_path(config)
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(schema_sql)
        await db.commit()


@asynccontextmanager
async def db_session(config: Config) -> AsyncIterator[aiosqlite.Connection]:
    db_path = get_db_path(config)
    db = await aiosqlite.connect(db_path)
    try:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        yield db
    finally:
        await db.close()
