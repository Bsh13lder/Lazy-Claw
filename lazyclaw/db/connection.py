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
            # Code Specialist visibility — captured per bg task that ran via
            # claude-code MCP. All five are encrypted-at-rest text columns so
            # users can inspect on the Code Specialist Web UI page what was
            # sent, what claude-code did, and where the files landed.
            #   mcp_prompt: the full instruction sent to the specialist
            #   mcp_transcript: encrypted JSON list of {tool, args, result, ms}
            #   workspace_dir: /workspace/<tag>/<goal>/<task_id>/
            #   files_touched: encrypted JSON list of relative file paths
            #   short_description: 1-line summary derived from instruction
            #   goal_id: Goal Executor goal id when dispatched by a goal
            ("background_tasks", "mcp_prompt", "ALTER TABLE background_tasks ADD COLUMN mcp_prompt TEXT"),
            ("background_tasks", "mcp_transcript", "ALTER TABLE background_tasks ADD COLUMN mcp_transcript TEXT"),
            ("background_tasks", "workspace_dir", "ALTER TABLE background_tasks ADD COLUMN workspace_dir TEXT"),
            ("background_tasks", "files_touched", "ALTER TABLE background_tasks ADD COLUMN files_touched TEXT"),
            ("background_tasks", "short_description", "ALTER TABLE background_tasks ADD COLUMN short_description TEXT"),
            ("background_tasks", "goal_id", "ALTER TABLE background_tasks ADD COLUMN goal_id TEXT"),
            ("note_links", "edge_type", "ALTER TABLE note_links ADD COLUMN edge_type TEXT NOT NULL DEFAULT 'wikilink'"),
            ("note_links", "source", "ALTER TABLE note_links ADD COLUMN source TEXT"),
            # LazyBrain Phase B: pipe-alias display text on resolved wikilinks.
            ("note_links", "display_text", "ALTER TABLE note_links ADD COLUMN display_text TEXT"),
            # LazyBrain PPR substrate (2026-05-21) — three additive columns
            # that let the link graph distinguish syntactic shape (wikilink
            # vs embed vs block_ref) AND preserve link counts so a note that
            # references [[X]] three times outranks one that references it
            # once. Orthogonal to ``edge_type`` (semantic relation: wikilink
            # | supersedes | contradicts | references | derives_from).
            #   anchor:     section / block id after ``#`` (nullable; raw case).
            #   link_kind:  syntactic shape — 'wikilink' | 'embed' | 'block_ref'.
            #   occurrence: how many times the same (from, to, anchor, kind)
            #               tuple appears in the note body. Obsidian counts
            #               multiple [[X]] mentions; the PPR random walk
            #               weights edges by occurrence.
            ("note_links", "anchor", "ALTER TABLE note_links ADD COLUMN anchor TEXT"),
            ("note_links", "link_kind", "ALTER TABLE note_links ADD COLUMN link_kind TEXT NOT NULL DEFAULT 'wikilink'"),
            ("note_links", "occurrence", "ALTER TABLE note_links ADD COLUMN occurrence INTEGER NOT NULL DEFAULT 1"),
            # LazyBrain Phase G: chunked embeddings dirty flag (mirrors embedding_dirty).
            ("notes", "chunks_dirty", "ALTER TABLE notes ADD COLUMN chunks_dirty INTEGER NOT NULL DEFAULT 1"),
            # LazyBrain Phase G+: per-chunk SHA1 of chunk_text. Lets
            # upsert_embedding skip the Ollama embed call when a chunk's
            # text is unchanged — 5-10x faster save on edits that touch
            # only one chunk in a multi-chunk note.
            ("note_chunks", "chunk_hash", "ALTER TABLE note_chunks ADD COLUMN chunk_hash TEXT"),
            # Bounty hunting — encrypted per-program session cookies for authenticated probes.
            ("bounty_programs", "cookies_jar", "ALTER TABLE bounty_programs ADD COLUMN cookies_jar TEXT"),
            ("bounty_programs", "cookies_saved_at", "ALTER TABLE bounty_programs ADD COLUMN cookies_saved_at TEXT"),
            # P1: Unified Code Session — each code-tagged goal owns ONE long-lived
            # worker session so recon → scaffold → iterate share context. Plaintext:
            # session IDs are random UUIDs assigned by Claude CLI, not user content.
            ("goals", "code_session_id", "ALTER TABLE goals ADD COLUMN code_session_id TEXT"),
            # Typed memory taxonomy (2026-05-20) — knowledge scope per note.
            # Mirrors Claude Code's auto-memory types: user|feedback|project|
            # reference|fact|session-log|other. Orthogonal to Capture.kind
            # (the auto_capture pattern tag). Auto-injection picks via
            # ``memory_type IN ('user','feedback','project','reference')``;
            # ``session-log`` becomes query-only, killing the daily_log
            # paraphrase contamination class that produced the 2026-05-19
            # 16:19 hallucination. Plaintext: scope label, not user content.
            # Default NULL so pre-migration rows are visible to a backfill
            # pass; ``is_auto_inject_type(None)`` fails closed to keep them
            # out of the cached system prompt until classified.
            ("notes", "memory_type", "ALTER TABLE notes ADD COLUMN memory_type TEXT"),
            # Reindex starvation fix (2026-05-20) — per-row cooldown timestamp.
            # Updated on EVERY reindex attempt (success or skip) so the
            # dirty-batch picker can order by ``last_reindex_attempt_at ASC
            # NULLS FIRST``. Without this, the picker's ``ORDER BY updated_at
            # DESC`` repeatedly served the same 3 most-recent dirty rows; if
            # they failed (Ollama 500 / decrypt-broken / tokenizer-busted)
            # the 3-consecutive-skip bailout fired and the other 451 dirty
            # rows starved forever. NULLS FIRST gives brand-new dirty rows
            # immediate priority (they have no attempt yet); failed rows fall
            # to the back of the queue, fair-share next cycle. Plaintext:
            # operational timestamp, no user content.
            ("notes", "last_reindex_attempt_at", "ALTER TABLE notes ADD COLUMN last_reindex_attempt_at TEXT"),
            # Budget ledger audit kind (2026-05-28) — distinguishes top-ups
            # ('credit') from Edit-budget audit rows ('edit'). DBs that
            # created budget_entries before this column was added would
            # otherwise fail to record edits.
            ("budget_entries", "kind", "ALTER TABLE budget_entries ADD COLUMN kind TEXT NOT NULL DEFAULT 'credit'"),
            # Per-task budget allocation (2026-05-28) — a task can carry its
            # own slice of the parent project budget (project 950 → task 500
            # → task tracks its own spent vs 500). Plaintext REAL like
            # projects.budget so totals SUM in SQL. Optional / nullable.
            ("tasks", "allocated_budget", "ALTER TABLE tasks ADD COLUMN allocated_budget REAL"),
            # Offline-sync primitives (feat/flutter-mobile) ───────────────
            # updated_at: last-write-wins timestamp bumped on every mutation.
            # Backfill: existing rows get created_at so the column is never
            # NULL; new rows are always stamped at insert time.
            ("tasks", "updated_at", "ALTER TABLE tasks ADD COLUMN updated_at TEXT"),
            # deleted_at: soft-delete tombstone. NULL = live; non-NULL = deleted.
            # Clients learn of deletes via the /changes delta feed instead of
            # rows silently disappearing from list responses.
            ("tasks", "deleted_at", "ALTER TABLE tasks ADD COLUMN deleted_at TEXT"),
            # Offline-sync tombstones for Notes + Budgets (feat/flutter-mobile).
            # deleted_at: NULL = live, non-NULL = deleted; clients learn of
            # deletes via each domain's /changes delta feed. updated_at already
            # exists on notes/projects/project_expenses and is bumped on update.
            ("notes", "deleted_at", "ALTER TABLE notes ADD COLUMN deleted_at TEXT"),
            ("projects", "deleted_at", "ALTER TABLE projects ADD COLUMN deleted_at TEXT"),
            ("project_expenses", "deleted_at", "ALTER TABLE project_expenses ADD COLUMN deleted_at TEXT"),
            # Per-project color (feat/flutter-mobile) — optional hex string like
            # "#4F8AF4" so the mobile calendar can color-code projects + tasks.
            # Plaintext like status/budget (not sensitive); NULL = no color.
            # Exposed via list + /api/budgets/changes for the offline sync pull.
            ("projects", "color", "ALTER TABLE projects ADD COLUMN color TEXT"),
            # Per-project favorite flag (feat/flutter-mobile) — pin important
            # projects so they surface in the mobile Home "Favorites" section
            # while the main Expenses list stays full. Stored PLAINTEXT as an
            # INTEGER 0/1 (not sensitive); default 0 so pre-migration rows are
            # un-favorited. Exposed via list + /api/budgets/changes for the
            # offline sync pull, exactly like `color`.
            ("projects", "is_favorite", "ALTER TABLE projects ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0"),
        ]
        for table, column, sql in migrations:
            try:
                row = await db.execute(f"PRAGMA table_info({table})")
                columns = [r[1] for r in await row.fetchall()]
                if column not in columns:
                    await db.execute(sql)
            except Exception:
                logger.debug("Migration %s.%s skipped (column may already exist)", table, column, exc_info=True)

        # Backfill: set updated_at = created_at for rows that existed before
        # the migration added the column. Safe to re-run on every init_db
        # (the WHERE guard makes it a no-op once all rows are stamped).
        try:
            await db.execute(
                "UPDATE tasks SET updated_at = created_at "
                "WHERE updated_at IS NULL AND created_at IS NOT NULL"
            )
        except Exception:
            logger.debug("tasks.updated_at backfill skipped", exc_info=True)

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

        # ── In-app notification feed (feat/flutter-mobile) ────────────────
        # Mirrors what is (or would have been) pushed to Telegram so the
        # mobile app can pull server-originated notifications it hasn't seen
        # yet via GET /api/notifications?since=. id/kind/created_at are
        # PLAINTEXT (needed for the since-query + client type rendering);
        # title/body are AES-256-GCM encrypted (user content). Created here
        # (not schema.sql) so upgrading installs pick it up — same rationale
        # as progress_templates above.
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS notifications ("
                "id TEXT PRIMARY KEY, "
                "user_id TEXT NOT NULL REFERENCES users(id), "
                "kind TEXT NOT NULL DEFAULT 'info', "
                "title TEXT, "
                "body TEXT, "
                "created_at TEXT NOT NULL DEFAULT (datetime('now'))"
                ")"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_notifications_user_created "
                "ON notifications(user_id, created_at)"
            )
        except Exception:
            logger.debug("notifications table migration skipped", exc_info=True)

        # meta: encrypted JSON sidecar for feed entries (thread_ref for
        # tap-to-open deep links on channel_message notifications).
        try:
            cols = await db.execute("PRAGMA table_info(notifications)")
            names = [r[1] for r in await cols.fetchall()]
            if "meta" not in names:
                await db.execute("ALTER TABLE notifications ADD COLUMN meta TEXT")
        except Exception:
            logger.debug("notifications.meta migration skipped", exc_info=True)

        # ── Unified-comms: channel inbox-thread metadata ──────────────────
        # Stores one encrypted row per (user, channel, contact) thread so the
        # mobile inbox can list threads, show previews, and track unread counts
        # without making a live channel read on every load.
        # Plaintext: id, user_id, channel, contact_handle, unread_count,
        #            last_activity, created_at, updated_at, deleted_at
        #            (needed for queries, sync delta feed, and tombstones).
        # Encrypted: contact_name, last_preview, last_seen_msg_id
        #            (user-visible content).
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS channel_threads ("
                "id TEXT PRIMARY KEY, "
                "user_id TEXT NOT NULL REFERENCES users(id), "
                "channel TEXT NOT NULL, "
                "contact_handle TEXT NOT NULL, "
                "contact_name TEXT, "
                "last_preview TEXT, "
                "unread_count INTEGER NOT NULL DEFAULT 0, "
                "last_activity TEXT NOT NULL DEFAULT (datetime('now')), "
                "last_seen_msg_id TEXT, "
                "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
                "updated_at TEXT NOT NULL DEFAULT (datetime('now')), "
                "deleted_at TEXT"
                ")"
            )
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_threads_unique "
                "ON channel_threads(user_id, channel, contact_handle)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_channel_threads_updated "
                "ON channel_threads(user_id, updated_at)"
            )
        except Exception:
            logger.debug("channel_threads migration skipped", exc_info=True)

        # ── Autonomous conversation runner: task state ────────────────────
        # One row per in-flight or completed conversation task. The agent
        # runs a back-and-forth exchange on behalf of the user (e.g. "ask
        # Alice on WhatsApp if she's coming") and writes outcomes here.
        #
        # Plaintext columns (needed for scheduler / status queries):
        #   id, user_id, channel, contact_handle, status, iteration,
        #   max_iterations, poll_interval, next_poll_at, created_at,
        #   last_activity_at, expires_at, approval_id
        # Encrypted columns (user-visible content, AES-256-GCM):
        #   contact_name, goal, completion_criteria, transcript_json,
        #   result, error
        try:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS conversation_tasks ("
                "id TEXT PRIMARY KEY, "
                "user_id TEXT NOT NULL REFERENCES users(id), "
                "channel TEXT NOT NULL, "
                "contact_handle TEXT NOT NULL, "
                "contact_name TEXT, "
                "goal TEXT, "
                "completion_criteria TEXT, "
                "status TEXT NOT NULL DEFAULT 'drafting', "
                "transcript_json TEXT, "
                "iteration INTEGER NOT NULL DEFAULT 0, "
                "max_iterations INTEGER NOT NULL DEFAULT 20, "
                "poll_interval INTEGER NOT NULL DEFAULT 60, "
                "next_poll_at TEXT, "
                "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
                "last_activity_at TEXT NOT NULL DEFAULT (datetime('now')), "
                "expires_at TEXT, "
                "result TEXT, "
                "error TEXT, "
                "approval_id TEXT"
                ")"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversation_due "
                "ON conversation_tasks(status, next_poll_at)"
            )
        except Exception:
            logger.debug("conversation_tasks migration skipped", exc_info=True)

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
                "chunk_hash  TEXT, "
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

        # Typed-memory index — created here, not in schema.sql, because on
        # upgrading installs the ``notes`` table already exists (so the
        # CREATE TABLE IF NOT EXISTS in schema.sql is a no-op and skips
        # the ``memory_type`` column) but the ALTER TABLE that adds it
        # only runs above. Putting CREATE INDEX in schema.sql crashed
        # init_db on 2026-05-20 — Docker rebuild looped because the
        # index referenced a column that didn't yet exist on the on-disk
        # schema. Same rationale as idx_chat_sessions_primary above.
        try:
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_notes_user_memory_type "
                "ON notes(user_id, memory_type)"
            )
        except Exception:
            logger.debug(
                "idx_notes_user_memory_type creation skipped", exc_info=True,
            )

        # Reindex queue index — accelerates the dirty-batch picker's
        # ``WHERE user_id=? AND (embedding_dirty=1 OR chunks_dirty=1)
        # ORDER BY last_reindex_attempt_at ASC NULLS FIRST`` lookup.
        # Created post-migration for the same reason as the typed-memory
        # index above: on upgrading installs the column is added by the
        # ALTER TABLE pass, so the index must come after. Docker-restart-
        # safe via IF NOT EXISTS.
        try:
            await db.execute(
                "CREATE INDEX IF NOT EXISTS "
                "idx_notes_user_reindex_attempt "
                "ON notes(user_id, last_reindex_attempt_at)"
            )
        except Exception:
            logger.debug(
                "idx_notes_user_reindex_attempt creation skipped",
                exc_info=True,
            )

        # Backlink-by-id index (2026-05-21) — "backlinks of resolved note N"
        # is the hot path for the upcoming PPR random walk and the existing
        # backlinks panel. Without this index ``WHERE user_id=? AND
        # to_note_id=?`` falls back to a text scan on to_page_name (which
        # only covers UNresolved links). Created post-migration because
        # to_note_id has been on the table from day one but the index
        # never existed; idempotent IF NOT EXISTS keeps repeated init_db
        # restarts a no-op.
        try:
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_note_links_to_note "
                "ON note_links(user_id, to_note_id)"
            )
        except Exception:
            logger.debug(
                "idx_note_links_to_note creation skipped", exc_info=True,
            )

        # Edge-type filter index — accelerates ``WHERE user_id=? AND
        # edge_type=?`` lookups (e.g. "all supersedes edges for user X").
        # Same post-migration placement as above: ``edge_type`` is added
        # by the ALTER TABLE pass on upgrade.
        try:
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_note_links_user_kind "
                "ON note_links(user_id, edge_type)"
            )
        except Exception:
            logger.debug(
                "idx_note_links_user_kind creation skipped", exc_info=True,
            )

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
