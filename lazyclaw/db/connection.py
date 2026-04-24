"""Database connection management with persistent connection pool.

Uses a single shared aiosqlite connection per database path. SQLite
serializes writes internally via WAL mode, so a shared connection is
safe and avoids the ~20-30ms overhead of connect/close per query.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from lazyclaw.config import Config

logger = logging.getLogger(__name__)

# Persistent connection pool: db_path → open connection
_pool: dict[str, aiosqlite.Connection] = {}


def get_db_path(config: Config) -> Path:
    return config.database_dir / "lazyclaw.db"


async def init_db(config: Config) -> None:
    config.database_dir.mkdir(parents=True, exist_ok=True)
    schema_path = Path(__file__).parent / "schema.sql"
    schema_sql = schema_path.read_text()

    db_path = get_db_path(config)
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(schema_sql)

        # Migrations — add columns that may not exist in older DBs
        migrations = [
            ("users", "role", "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'"),
            ("users", "settings", "ALTER TABLE users ADD COLUMN settings TEXT DEFAULT '{}'"),
            ("users", "encrypted_dek", "ALTER TABLE users ADD COLUMN encrypted_dek TEXT"),
            ("mcp_connections", "favorite", "ALTER TABLE mcp_connections ADD COLUMN favorite INTEGER DEFAULT 0"),
            ("users", "password_encrypted_dek", "ALTER TABLE users ADD COLUMN password_encrypted_dek TEXT"),
            ("users", "recovery_encrypted_dek", "ALTER TABLE users ADD COLUMN recovery_encrypted_dek TEXT"),
            ("background_tasks", "cost_usd", "ALTER TABLE background_tasks ADD COLUMN cost_usd REAL DEFAULT 0.0"),
            ("background_tasks", "tokens_used", "ALTER TABLE background_tasks ADD COLUMN tokens_used INTEGER DEFAULT 0"),
            ("background_tasks", "llm_calls", "ALTER TABLE background_tasks ADD COLUMN llm_calls INTEGER DEFAULT 0"),
            # Task execution tracking — saved-but-unexecuted gap fix
            ("tasks", "last_error", "ALTER TABLE tasks ADD COLUMN last_error TEXT"),
            ("tasks", "attempt_count", "ALTER TABLE tasks ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0"),
            ("tasks", "last_attempted_at", "ALTER TABLE tasks ADD COLUMN last_attempted_at TEXT"),
            ("tasks", "trace_session_id", "ALTER TABLE tasks ADD COLUMN trace_session_id TEXT"),
            ("tasks", "lazybrain_note_id", "ALTER TABLE tasks ADD COLUMN lazybrain_note_id TEXT"),
            # Sub-task steps — encrypted JSON array of {id, title, done}.
            ("tasks", "steps", "ALTER TABLE tasks ADD COLUMN steps TEXT"),
            # Plan Mode — per-user toggle for Claude-Code-style approval gate.
            ("users", "auto_plan", "ALTER TABLE users ADD COLUMN auto_plan INTEGER NOT NULL DEFAULT 1"),
            # Cross-channel history — primary session flag on chat sessions.
            ("agent_chat_sessions", "is_primary", "ALTER TABLE agent_chat_sessions ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0"),
        ]
        for table, column, sql in migrations:
            try:
                row = await db.execute(f"PRAGMA table_info({table})")
                columns = [r[1] for r in await row.fetchall()]
                if column not in columns:
                    await db.execute(sql)
            except Exception:
                logger.debug("Migration %s.%s skipped (column may already exist)", table, column, exc_info=True)

        # Partial unique index — enforces one primary session per user.
        # Created after the column migration above in case we're upgrading an
        # older DB where the column didn't exist when schema.sql first ran.
        try:
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_sessions_primary "
                "ON agent_chat_sessions(user_id) WHERE is_primary = 1"
            )
        except Exception:
            logger.debug("idx_chat_sessions_primary creation skipped", exc_info=True)

        await db.commit()


@asynccontextmanager
async def db_session(config: Config) -> AsyncIterator[aiosqlite.Connection]:
    """Get a database connection from the persistent pool.

    Reuses a single connection per database path instead of opening
    and closing on every call (~20-30ms savings per query).
    """
    db_path = str(get_db_path(config))

    if db_path not in _pool:
        db = await aiosqlite.connect(db_path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        _pool[db_path] = db
        logger.debug("Opened persistent DB connection: %s", db_path)

    yield _pool[db_path]


async def close_pool() -> None:
    """Close all pooled connections. Call on shutdown."""
    for db_path, conn in list(_pool.items()):
        try:
            await conn.close()
            logger.debug("Closed pooled DB connection: %s", db_path)
        except Exception as exc:
            logger.debug("Failed to close pooled DB connection %s: %s", db_path, exc)
    _pool.clear()
