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
            # Advance reminders — JSON array of pending ISO timestamps that fire
            # before reminder_at (e.g. T-2h, T-1h for important appointments).
            ("tasks", "pre_reminders", "ALTER TABLE tasks ADD COLUMN pre_reminders TEXT"),
            # Atomic nag claim — set on UPDATE..WHERE so a heartbeat crash
            # mid-fire can't double-send. Boot sweep clears claims older
            # than 5 minutes so a hard crash doesn't permanently block.
            ("tasks", "nag_fired_at", "ALTER TABLE tasks ADD COLUMN nag_fired_at TEXT"),
            # Recurring offset — minutes between due_date and reminder_at.
            # Stored once on create so recurring respawn doesn't have to
            # reparse-and-fallback (the silent-drift bug).
            ("tasks", "reminder_offset_minutes", "ALTER TABLE tasks ADD COLUMN reminder_offset_minutes INTEGER"),
            # Progress tracking — encrypted JSON timeline of {ts, kind, text, source}
            # entries. Drives /progress, ask_about_task summaries, EOD nudges.
            ("tasks", "progress_log", "ALTER TABLE tasks ADD COLUMN progress_log TEXT"),
            # Pulse cadence — cron expression set when user opts a task into
            # check-in pulses. Heartbeat creates/manages a paired agent_jobs row.
            ("tasks", "check_every", "ALTER TABLE tasks ADD COLUMN check_every TEXT"),
            # FK to progress_templates.id — drives the questions/buttons for pulses.
            ("tasks", "progress_template_id", "ALTER TABLE tasks ADD COLUMN progress_template_id TEXT"),
            # Stale-nudge state machine — set when a 4h-silent in_progress task
            # gets the soft "still going?" ping; cleared on user response or
            # task completion. Prevents nudge loops on every heartbeat tick.
            ("tasks", "nudge_sent_at", "ALTER TABLE tasks ADD COLUMN nudge_sent_at TEXT"),
            # Last pulse fire timestamp — used for cooldown so the stale-
            # detector doesn't fire 30m after a pulse already pinged.
            ("tasks", "last_pulse_fired_at", "ALTER TABLE tasks ADD COLUMN last_pulse_fired_at TEXT"),
            # Plan Mode — per-user toggle for Claude-Code-style approval gate.
            ("users", "auto_plan", "ALTER TABLE users ADD COLUMN auto_plan INTEGER NOT NULL DEFAULT 1"),
            # Cross-channel history — primary session flag on chat sessions.
            ("agent_chat_sessions", "is_primary", "ALTER TABLE agent_chat_sessions ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0"),
            # Job run outcome tracking — surfaces success/failure on Jobs UI.
            ("agent_jobs", "last_status", "ALTER TABLE agent_jobs ADD COLUMN last_status TEXT"),
            ("agent_jobs", "last_error", "ALTER TABLE agent_jobs ADD COLUMN last_error TEXT"),
            # Browser-template auto-save metrics — drives upsert-by-host + compaction.
            ("browser_templates", "run_count", "ALTER TABLE browser_templates ADD COLUMN run_count INTEGER NOT NULL DEFAULT 0"),
            ("browser_templates", "success_count", "ALTER TABLE browser_templates ADD COLUMN success_count INTEGER NOT NULL DEFAULT 0"),
            ("browser_templates", "last_run_at", "ALTER TABLE browser_templates ADD COLUMN last_run_at TEXT"),
            ("browser_templates", "auto_saved", "ALTER TABLE browser_templates ADD COLUMN auto_saved INTEGER NOT NULL DEFAULT 0"),
            ("browser_templates", "corrections_pending", "ALTER TABLE browser_templates ADD COLUMN corrections_pending INTEGER NOT NULL DEFAULT 0"),
            ("browser_templates", "version", "ALTER TABLE browser_templates ADD COLUMN version INTEGER NOT NULL DEFAULT 1"),
            # Second-Brain Substrate (Phase 2+4) — never-forget + typed edges.
            #   notes.embedding_dirty: 1 = content changed since last embed; reindex daemon picks these up.
            #   notes.archived:        1 = hide from default recall (skills-vault toggle, archived plans, etc.)
            #   notes.aliases:         JSON array of plaintext aliases for [[wikilink]] resolution.
            #   background_tasks.lazybrain_note_id: backlink to mirrored research note.
            #   note_links.edge_type:  wikilink | supersedes | contradicts | references | derives_from.
            #   note_links.source:     skill | user | auto — provenance for the edge.
            ("notes", "embedding_dirty", "ALTER TABLE notes ADD COLUMN embedding_dirty INTEGER NOT NULL DEFAULT 0"),
            ("notes", "archived", "ALTER TABLE notes ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"),
            ("notes", "aliases", "ALTER TABLE notes ADD COLUMN aliases TEXT"),
            ("background_tasks", "lazybrain_note_id", "ALTER TABLE background_tasks ADD COLUMN lazybrain_note_id TEXT"),
            ("note_links", "edge_type", "ALTER TABLE note_links ADD COLUMN edge_type TEXT NOT NULL DEFAULT 'wikilink'"),
            ("note_links", "source", "ALTER TABLE note_links ADD COLUMN source TEXT"),
            # LazyBrain Phase B: pipe-alias display text on resolved wikilinks.
            ("note_links", "display_text", "ALTER TABLE note_links ADD COLUMN display_text TEXT"),
            # LazyBrain Phase G: chunked embeddings dirty flag (mirrors embedding_dirty).
            ("notes", "chunks_dirty", "ALTER TABLE notes ADD COLUMN chunks_dirty INTEGER NOT NULL DEFAULT 1"),
        ]
        for table, column, sql in migrations:
            try:
                row = await db.execute(f"PRAGMA table_info({table})")
                columns = [r[1] for r in await row.fetchall()]
                if column not in columns:
                    await db.execute(sql)
            except Exception:
                logger.debug("Migration %s.%s skipped (column may already exist)", table, column, exc_info=True)

        # ── Progress templates — bundled function + learned skill ─────────
        # Schema cloned from browser_templates: name + playbook are encrypted,
        # category match + cron + auto-save metrics are plaintext for the
        # daemon's tick-time queries. questions are encrypted (free-form NL),
        # buttons are plaintext (label + structured action callback).
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS progress_templates ("
                "id TEXT PRIMARY KEY, "
                "user_id TEXT NOT NULL REFERENCES users(id), "
                "name TEXT NOT NULL, "
                "applies_to_category TEXT, "
                "every TEXT NOT NULL, "
                "questions TEXT, "
                "buttons TEXT, "
                "auto_saved INTEGER NOT NULL DEFAULT 0, "
                "run_count INTEGER NOT NULL DEFAULT 0, "
                "success_count INTEGER NOT NULL DEFAULT 0, "
                "last_run_at TEXT, "
                "version INTEGER NOT NULL DEFAULT 1, "
                "created_at TEXT DEFAULT (datetime('now'))"
                ")"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_progress_templates_user_cat "
                "ON progress_templates(user_id, applies_to_category)"
            )
        except Exception:
            logger.debug("progress_templates table migration skipped", exc_info=True)

        # ── LazyBrain Phase A: hybrid retrieval substrate ──────────────────
        # Phase F adds BM25 (FTS5) ranking that fuses with cosine via RRF.
        # Phase G adds chunk-level embeddings + chunk-level BM25.
        #
        # Threat-model note: ``notes_fts`` indexes only ``title_key`` (already
        # plaintext per the existing wikilink-resolution design). ``note_chunks_fts``
        # indexes the chunk text in plaintext — a deliberate tradeoff for BM25
        # recall on rare proper nouns / code identifiers. Users who consider
        # content tokens too sensitive can drop ``note_chunks_fts`` and
        # ``hybrid_search`` will fall back to title-FTS + dense.
        substrate_migrations: list[tuple[str, str]] = [
            (
                "notes_fts",
                # Contentless: we manage rowids ourselves, look up note rows
                # via the unindexed note_id column. unicode61+remove_diacritics
                # mirrors store._fold() so accented + non-accented forms match.
                "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5("
                "note_id UNINDEXED, user_id UNINDEXED, title, "
                "tokenize = 'unicode61 remove_diacritics 2'"
                ")",
            ),
            (
                "note_chunks",
                # Encrypted vector blob per chunk. Same envelope shape as
                # note_embeddings; PRIMARY KEY on (note_id, chunk_idx).
                "CREATE TABLE IF NOT EXISTS note_chunks ("
                "note_id     TEXT NOT NULL, "
                "user_id     TEXT NOT NULL, "
                "chunk_idx   INTEGER NOT NULL, "
                "model       TEXT NOT NULL, "
                "dim         INTEGER NOT NULL, "
                "vector      TEXT NOT NULL, "
                "chunk_text  TEXT, "
                "updated_at  TEXT DEFAULT (datetime('now')), "
                "PRIMARY KEY (note_id, chunk_idx), "
                "FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE"
                ")",
            ),
            (
                "idx_note_chunks_user",
                "CREATE INDEX IF NOT EXISTS idx_note_chunks_user "
                "ON note_chunks(user_id, model, dim)",
            ),
            (
                "note_chunks_fts",
                # Plaintext chunk text for BM25 — see threat-model comment above.
                "CREATE VIRTUAL TABLE IF NOT EXISTS note_chunks_fts USING fts5("
                "note_id UNINDEXED, user_id UNINDEXED, chunk_idx UNINDEXED, "
                "chunk_text, "
                "tokenize = 'unicode61 remove_diacritics 2'"
                ")",
            ),
        ]
        for name, sql in substrate_migrations:
            try:
                await db.execute(sql)
            except Exception:
                logger.debug("Substrate migration %s skipped", name, exc_info=True)

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
