"""Ingest Claude Code plan files into LazyBrain.

Claude Code's plan-mode artifacts live as markdown at
``~/.claude/plans/*.md``. The user has 80+ of them — real engineering
history. Without ingestion they're invisible to the agent's recall
pipeline (RAG, semantic search, hybrid memory picker).

This module is idempotent: a plan is upserted as a single LazyBrain note
keyed off the plan's slug (filename without extension). The mtime of the
file is stored in frontmatter; on subsequent ticks the file is skipped
unless its mtime is newer than the stored value.

Tags: ``kind/plan owner/user source/claude-plan``.
Honors per-user opt-out via ``users.settings.ingest_claude_plans=false``.
Never raises — every failure logs at debug and the daemon moves on.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from lazyclaw.config import Config
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)


# Plan files live at ~/.claude/plans by default. Keep the resolution
# loose so users running the daemon under a different HOME (e.g.
# Docker containers with mounted plan dirs) can override via env.
def _plans_dir() -> Path:
    raw = os.environ.get("LAZYCLAW_CLAUDE_PLANS_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".claude" / "plans"


# Cap how many plans we ingest in one tick — large backlog should drain
# over a handful of ticks rather than block the daemon.
_PER_TICK_LIMIT = 30

# Body cap so a long plan doesn't dominate the importance pool. The
# original file stays untouched on disk; this is just the brain-resident
# excerpt. 8 KB is enough for the context-section + first phase, which
# is what recall actually needs.
_BODY_CAP_CHARS = 8000


async def _user_opted_out(config: Config, user_id: str) -> bool:
    """Return True if the user explicitly disabled plan ingestion."""
    try:
        async with db_session(config) as db:
            row = await db.execute(
                "SELECT settings FROM users WHERE id = ?", (user_id,),
            )
            r = await row.fetchone()
        if not r or not r[0]:
            return False
        try:
            settings = json.loads(r[0])
        except (json.JSONDecodeError, TypeError):
            return False
        return settings.get("ingest_claude_plans") is False
    except Exception:
        return False


async def _existing_plan_note(
    config: Config, user_id: str, slug: str,
) -> dict | None:
    """Look up the existing plan note for ``slug`` if any."""
    try:
        from lazyclaw.lazybrain import store as lb_store
        from lazyclaw.lazybrain.wikilinks import normalize_page
    except Exception:
        return None
    title_key = normalize_page(_title_for_slug(slug))
    try:
        async with db_session(config) as db:
            row = await db.execute(
                "SELECT id FROM notes WHERE user_id = ? AND title_key = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (user_id, title_key),
            )
            r = await row.fetchone()
        if not r:
            return None
        return await lb_store.get_note(config, user_id, r[0])
    except Exception:
        return None


def _title_for_slug(slug: str) -> str:
    """Human-friendly title from the slugged filename."""
    return f"Plan · {slug.replace('-', ' ')}"


def _plan_mtime(props: dict) -> float | None:
    raw = props.get("source_mtime")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


async def ingest_claude_plans(config: Config, user_id: str) -> dict:
    """Walk ~/.claude/plans/*.md and upsert each as a LazyBrain note.

    Returns ``{checked, ingested, skipped, opted_out}``. Per-call cost is
    near-zero when nothing changed: each plan is only opened if its mtime
    differs from the stored frontmatter value.
    """
    if await _user_opted_out(config, user_id):
        return {"checked": 0, "ingested": 0, "skipped": 0, "opted_out": True}

    plans_dir = _plans_dir()
    if not plans_dir.exists():
        return {"checked": 0, "ingested": 0, "skipped": 0, "opted_out": False}

    try:
        from lazyclaw.lazybrain import store as lb_store
        from lazyclaw.lazybrain.frontmatter import parse_frontmatter
    except Exception:
        logger.debug("lazybrain store import failed in ingest_claude_plans")
        return {"checked": 0, "ingested": 0, "skipped": 0, "opted_out": False}

    files = sorted(plans_dir.glob("*.md"))
    checked = 0
    ingested = 0
    skipped = 0

    for path in files:
        if checked >= _PER_TICK_LIMIT:
            break
        checked += 1
        try:
            stat = path.stat()
        except OSError:
            continue
        mtime = stat.st_mtime
        slug = path.stem

        existing = await _existing_plan_note(config, user_id, slug)
        if existing:
            try:
                props, _body, _ = parse_frontmatter(existing.get("content") or "")
                old_mtime = _plan_mtime(props)
            except Exception:
                old_mtime = None
            if old_mtime is not None and abs(old_mtime - mtime) < 1.0:
                skipped += 1
                continue

        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        body = raw.strip()
        if len(body) > _BODY_CAP_CHARS:
            body = body[: _BODY_CAP_CHARS - 1].rstrip() + "…"

        title = _title_for_slug(slug)
        tags = ["kind/plan", "owner/user", "source/claude-plan", "auto"]
        frontmatter = {
            "kind": "plan",
            "source": "claude-plan",
            "source_path": str(path),
            "source_mtime": mtime,
            "slug": slug,
        }

        try:
            if existing:
                await lb_store.update_note(
                    config, user_id, existing["id"],
                    content=body,
                    title=title,
                    tags=tags,
                    frontmatter_updates=frontmatter,
                )
            else:
                await lb_store.save_note(
                    config, user_id,
                    content=body,
                    title=title,
                    tags=tags,
                    importance=4,  # below user-saved memories, above noise
                    frontmatter=frontmatter,
                )
            ingested += 1
        except Exception:
            logger.debug("plan ingest failed for %s", path, exc_info=True)
            skipped += 1

    if ingested:
        logger.info(
            "claude plan ingest: user=%s ingested=%d skipped=%d (of %d)",
            user_id, ingested, skipped, checked,
        )
    return {
        "checked": checked,
        "ingested": ingested,
        "skipped": skipped,
        "opted_out": False,
    }


__all__ = ["ingest_claude_plans"]
