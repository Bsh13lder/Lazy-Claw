from __future__ import annotations

import json
import logging
import time

import aiosqlite

from mcp_apihunter.models import RegistryEntry

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS endpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    api_key_env TEXT,
    models_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    last_validated REAL,
    latency_avg_ms REAL,
    added_by TEXT NOT NULL,
    created_at REAL NOT NULL
)
"""


def _row_to_entry(row: aiosqlite.Row) -> RegistryEntry:
    """Convert a sqlite row to a frozen RegistryEntry."""
    return RegistryEntry(
        id=row["id"],
        name=row["name"],
        base_url=row["base_url"],
        api_key_env=row["api_key_env"],
        models=tuple(json.loads(row["models_json"])),
        status=row["status"],
        last_validated=row["last_validated"],
        latency_avg_ms=row["latency_avg_ms"],
        added_by=row["added_by"],
        created_at=row["created_at"],
    )


class Registry:
    """SQLite-backed registry for API endpoints."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is None:
            self._db = await aiosqlite.connect(self._db_path)
            self._db.row_factory = aiosqlite.Row
        return self._db

    async def init_db(self) -> None:
        """Create the endpoints table if it does not exist."""
        db = await self._get_db()
        await db.execute(_CREATE_TABLE)
        await db.commit()
        logger.info("Registry database initialized at %s", self._db_path)

    async def add(
        self,
        name: str,
        base_url: str,
        api_key_env: str | None,
        models: list[str],
        added_by: str,
    ) -> RegistryEntry:
        """Insert a new endpoint and return it with its assigned id."""
        now = time.time()
        models_json = json.dumps(models)
        db = await self._get_db()
        cursor = await db.execute(
            "INSERT INTO endpoints (name, base_url, api_key_env, models_json, status, added_by, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (name, base_url, api_key_env, models_json, added_by, now),
        )
        await db.commit()
        entry_id = cursor.lastrowid

        return RegistryEntry(
            id=entry_id,
            name=name,
            base_url=base_url,
            api_key_env=api_key_env,
            models=tuple(models),
            status="pending",
            last_validated=None,
            latency_avg_ms=None,
            added_by=added_by,
            created_at=now,
        )

    async def update_status(
        self,
        entry_id: int,
        status: str,
        latency_avg_ms: float | None,
        last_validated: float,
    ) -> RegistryEntry | None:
        """Update an entry's status and return the updated entry."""
        db = await self._get_db()
        await db.execute(
            "UPDATE endpoints SET status = ?, latency_avg_ms = ?, last_validated = ? WHERE id = ?",
            (status, latency_avg_ms, last_validated, entry_id),
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM endpoints WHERE id = ?", (entry_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_entry(row)

    async def remove(self, entry_id: int) -> bool:
        """Mark an entry as removed. Returns True if the entry existed."""
        db = await self._get_db()
        cursor = await db.execute(
            "UPDATE endpoints SET status = 'removed' WHERE id = ? AND status != 'removed'",
            (entry_id,),
        )
        await db.commit()
        return cursor.rowcount > 0

    async def list_all(self, status_filter: str | None = None) -> list[RegistryEntry]:
        """List all endpoints, optionally filtered by status."""
        db = await self._get_db()
        if status_filter:
            cursor = await db.execute(
                "SELECT * FROM endpoints WHERE status = ? ORDER BY created_at DESC",
                (status_filter,),
            )
        else:
            cursor = await db.execute("SELECT * FROM endpoints ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [_row_to_entry(row) for row in rows]

    async def search(self, query: str) -> list[RegistryEntry]:
        """Search endpoints by name, base_url, or models."""
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM endpoints WHERE "
            "(name LIKE ? ESCAPE '\\' OR base_url LIKE ? ESCAPE '\\' OR models_json LIKE ? ESCAPE '\\') "
            "AND status != 'removed' "
            "ORDER BY created_at DESC",
            (pattern, pattern, pattern),
        )
        rows = await cursor.fetchall()
        return [_row_to_entry(row) for row in rows]

    async def get(self, entry_id: int) -> RegistryEntry | None:
        """Get a single endpoint by id."""
        db = await self._get_db()
        cursor = await db.execute("SELECT * FROM endpoints WHERE id = ?", (entry_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_entry(row)
