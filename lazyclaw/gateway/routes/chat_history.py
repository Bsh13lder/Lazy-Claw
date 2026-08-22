"""Chat History API — session CRUD and message retrieval."""

from __future__ import annotations

import json
import re
import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lazyclaw.browser.action_planner import strip_plan_json_block
from lazyclaw.config import load_config
from lazyclaw.crypto.encryption import decrypt_field
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.memory.chat_message_store import is_notification_card_metadata
from lazyclaw.runtime.consolidation_guidance import is_consolidation_turn
from lazyclaw.runtime.session_resolver import (
    get_primary_session_id,
    invalidate_primary_session,
)
from lazyclaw.runtime.turn_markers import BACKGROUND_TURN_PREFIXES
from lazyclaw.skills.tool_namespace import bare_tool_name

logger = logging.getLogger(__name__)

_config = load_config()

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Cap for the tool-result preview joined onto history tool-call entries.
_TOOL_RESULT_PREVIEW_CHARS = 500

# Internal reasoning blocks the agent writes before its user-facing text.
# Closed blocks are removed whole; a dangling opening tag (stream cut mid-
# plan) drops through to the bare-tag scrub so no raw XML ever ships.
_INTERNAL_BLOCK_RE = re.compile(
    r"<(plan|taor_plan|think)>.*?</\1>\s*", re.DOTALL,
)
_INTERNAL_BARE_TAG_RE = re.compile(r"</?(plan|taor_plan|think)>\s*")


def _strip_internal_blocks(content: str) -> str:
    """Remove <plan>/<taor_plan>/<think> reasoning blocks for display.

    Also drops the action planner's plan-JSON block (schema-keyed, see
    strip_plan_json_block) — rows stored before the 2026-08-20 write-side
    strip still carry it, so the read side heals them."""
    out = _INTERNAL_BLOCK_RE.sub("", content)
    out = _INTERNAL_BARE_TAG_RE.sub("", out)
    if '"steps"' in out:
        out = strip_plan_json_block(out)
    return out.lstrip()


def _extract_tool_calls(metadata_raw: str | None) -> list | None:
    """Parse stored message metadata into a tool-call list, fail-soft.

    Two shapes exist on disk: the 2026-06-10 metadata-encryption pass
    stores the tool-call list directly (``[...]``); older rows wrap it
    as ``{"tool_calls": [...]}``. Anything unparseable (including the
    ``[encrypted]`` decrypt fallback sentinel) returns None — a bad row
    must never take down the whole history response.
    """
    if not metadata_raw:
        return None
    try:
        meta = json.loads(metadata_raw)
    except (json.JSONDecodeError, TypeError):
        logger.debug("Failed to parse message metadata JSON", exc_info=True)
        return None
    if isinstance(meta, list):
        return meta
    if isinstance(meta, dict):
        tool_calls = meta.get("tool_calls")
        return tool_calls if isinstance(tool_calls, list) else None
    return None


def _enrich_tool_calls(tool_calls: list, tool_results: dict) -> list:
    """Read-time enrichment of stored tool-call entries (retroactive —
    heals every existing row without rewriting anything on disk).

    Each well-formed entry gains:

    * ``display`` — ``bare_tool_name(name)`` (strips the unstable
      ``mcp_<server-uuid>_`` prefix; plain names pass through);
    * ``result`` — the matching ``role="tool"`` row's content capped at
      ``_TOOL_RESULT_PREVIEW_CHARS``, only when a match exists;
    * ``status`` — ``"done"`` when a matching tool row exists on the
      loaded page, else ``"unknown"``.

    Fail-soft per entry: malformed entries (non-dict, junk ids) pass
    through untouched — one bad row must never 500 the whole history.
    Returns NEW dicts; the parsed input entries are never mutated.
    """
    enriched: list = []
    for entry in tool_calls:
        if not isinstance(entry, dict):
            enriched.append(entry)
            continue
        try:
            name = entry.get("name")
            display = bare_tool_name(name) if isinstance(name, str) else name
            tc_id = entry.get("id")
            result = tool_results.get(tc_id) if isinstance(tc_id, str) else None
            new_entry = {
                **entry,
                "display": display,
                "status": "done" if result is not None else "unknown",
            }
            if result is not None:
                new_entry["result"] = result[:_TOOL_RESULT_PREVIEW_CHARS]
            enriched.append(new_entry)
        except Exception:
            logger.debug("Tool-call enrichment failed for entry", exc_info=True)
            enriched.append(entry)
    return enriched


class CreateSessionRequest(BaseModel):
    title: str = Field(default="New Chat", max_length=200)


class UpdateSessionRequest(BaseModel):
    title: str | None = None
    archived: bool | None = None


@router.get("/sessions")
async def list_sessions(user: User = Depends(get_current_user)):
    """List user's chat sessions (non-archived, newest first).

    Also repairs orphaned messages — creates missing session rows
    for any chat_session_id that has messages but no session entry —
    and ensures the user has a primary session (the shared bucket that
    Telegram / CLI / TUI / REPL all write into).
    """
    # Ensure the user has a primary session. First call on a fresh DB
    # promotes the oldest existing session or creates "Main".
    await get_primary_session_id(_config, user.id)

    async with db_session(_config) as db:
        # Repair orphaned messages — create missing session rows
        await db.execute(
            "INSERT OR IGNORE INTO agent_chat_sessions (id, user_id, message_count) "
            "SELECT m.chat_session_id, m.user_id, COUNT(*) "
            "FROM agent_messages m "
            "LEFT JOIN agent_chat_sessions s ON s.id = m.chat_session_id "
            "WHERE m.user_id = ? AND s.id IS NULL "
            "AND m.chat_session_id IS NOT NULL "
            "GROUP BY m.chat_session_id",
            (user.id,),
        )
        await db.commit()

        rows = await db.execute(
            "SELECT id, title, message_count, is_primary, created_at "
            "FROM agent_chat_sessions "
            "WHERE user_id = ? AND archived_at IS NULL "
            "ORDER BY is_primary DESC, created_at DESC",
            (user.id,),
        )
        sessions = [
            {
                "id": r[0],
                "title": r[1] or "New Chat",
                "message_count": r[2] or 0,
                "is_primary": bool(r[3]),
                "created_at": r[4],
            }
            for r in await rows.fetchall()
        ]
    return {"sessions": sessions}


@router.post("/sessions")
async def create_session(
    body: CreateSessionRequest,
    user: User = Depends(get_current_user),
):
    """Create a new chat session."""
    logger.debug(
        "[route:chat] POST session user=%s fields=%s",
        user.id, list(body.model_dump(exclude_unset=True).keys()),
    )
    session_id = str(uuid4())
    async with db_session(_config) as db:
        await db.execute(
            "INSERT INTO agent_chat_sessions (id, user_id, title) VALUES (?, ?, ?)",
            (session_id, user.id, body.title),
        )
        await db.commit()
    return {"id": session_id, "title": body.title}


@router.patch("/sessions/{session_id}")
async def update_session(
    session_id: str,
    body: UpdateSessionRequest,
    user: User = Depends(get_current_user),
):
    """Rename or archive a chat session.

    Archiving the primary session is blocked: it would hide the shared
    cross-channel history from the Web UI while Telegram / CLI keep writing
    into it — a confusing silent failure.
    """
    logger.debug(
        "[route:chat] PATCH session id=%s user=%s fields=%s",
        session_id, user.id, list(body.model_dump(exclude_unset=True).keys()),
    )
    async with db_session(_config) as db:
        row = await db.execute(
            "SELECT id, is_primary FROM agent_chat_sessions WHERE id = ? AND user_id = ?",
            (session_id, user.id),
        )
        found = await row.fetchone()
        if not found:
            logger.warning(
                "[route:chat] PATCH session id=%s user=%s -> 404 session not found",
                session_id, user.id,
            )
            raise HTTPException(status_code=404, detail="Session not found")

        if body.archived is True and found[1]:
            logger.warning(
                "[route:chat] PATCH session id=%s user=%s -> 409 cannot archive primary session",
                session_id, user.id,
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot archive the primary session — it's the shared "
                    "history bucket for Telegram, CLI, TUI, and REPL."
                ),
            )

        if body.title is not None:
            await db.execute(
                "UPDATE agent_chat_sessions SET title = ? WHERE id = ? AND user_id = ?",
                (body.title, session_id, user.id),
            )
        if body.archived is True:
            await db.execute(
                "UPDATE agent_chat_sessions SET archived_at = datetime('now') WHERE id = ? AND user_id = ?",
                (session_id, user.id),
            )
        await db.commit()
    return {"status": "updated"}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user: User = Depends(get_current_user),
):
    """Delete a chat session and all its messages.

    The primary session (shared across channels) cannot be deleted. Empty it
    with PATCH { archived: true } or clear the messages via the agent's
    /clear flow instead — deleting it would orphan Telegram/CLI history.
    """
    async with db_session(_config) as db:
        row = await db.execute(
            "SELECT id, is_primary FROM agent_chat_sessions WHERE id = ? AND user_id = ?",
            (session_id, user.id),
        )
        found = await row.fetchone()
        if not found:
            logger.warning(
                "[route:chat] DELETE session id=%s user=%s -> 404 session not found",
                session_id, user.id,
            )
            raise HTTPException(status_code=404, detail="Session not found")

        if found[1]:  # is_primary = 1
            logger.warning(
                "[route:chat] DELETE session id=%s user=%s -> 409 cannot delete primary session",
                session_id, user.id,
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot delete the primary session — it's the shared "
                    "history bucket for Telegram, CLI, TUI, and REPL. "
                    "Archive or clear its messages instead."
                ),
            )

        await db.execute(
            "DELETE FROM agent_messages WHERE chat_session_id = ? AND user_id = ?",
            (session_id, user.id),
        )
        await db.execute(
            "DELETE FROM agent_chat_sessions WHERE id = ? AND user_id = ?",
            (session_id, user.id),
        )
        await db.commit()

    invalidate_primary_session(user.id)
    return {"status": "deleted"}


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    limit: int = 50,
    before: str | None = None,
    user: User = Depends(get_current_user),
):
    """Load decrypted messages for a chat session (paginated).

    Clients seed their chat screens from this endpoint, so the default page
    is the NEWEST `limit` messages; `before=<message_id>` pages upward
    through older history. Both pages are returned oldest-first, ready to
    render top-to-bottom.

    Turns are batch-persisted with one shared created_at (agent.py
    post-loop), so ordering and the `before` anchor use (created_at, rowid)
    — created_at alone would skip or reshuffle same-timestamp siblings.
    """
    key = await get_user_dek(_config, user.id)

    async with db_session(_config) as db:
        # Verify session belongs to user
        row = await db.execute(
            "SELECT id FROM agent_chat_sessions WHERE id = ? AND user_id = ?",
            (session_id, user.id),
        )
        if not await row.fetchone():
            logger.warning(
                "[route:chat] GET session messages id=%s user=%s -> 404 session not found",
                session_id, user.id,
            )
            raise HTTPException(status_code=404, detail="Session not found")

        if before:
            rows = await db.execute(
                "SELECT id, role, content, tool_name, metadata, created_at "
                "FROM agent_messages "
                "WHERE user_id = ? AND chat_session_id = ? "
                "AND (created_at, rowid) < "
                "(SELECT created_at, rowid FROM agent_messages WHERE id = ?) "
                "ORDER BY created_at DESC, rowid DESC "
                "LIMIT ?",
                (user.id, session_id, before, limit),
            )
        else:
            rows = await db.execute(
                "SELECT id, role, content, tool_name, metadata, created_at "
                "FROM agent_messages "
                "WHERE user_id = ? AND chat_session_id = ? "
                "ORDER BY created_at DESC, rowid DESC "
                "LIMIT ?",
                (user.id, session_id, limit),
            )

        fetched = [
            (r, decrypt_field(r[2], key) or "")
            for r in await rows.fetchall()
        ]

        # tool_call_id → result text, from the role="tool" rows on this
        # page (their `tool_name` column holds the originating
        # tool_call_id — see agent.py's persistence loop). A result row
        # outside the loaded page leaves its call entry status="unknown".
        tool_results = {
            r[3]: content
            for r, content in fetched
            if r[1] == "tool" and r[3]
        }

        # [SILENT]-prefixed replies are the suppress-everywhere contract
        # (no Telegram push, no feed row, no ping) — but the agent turn
        # still persists them, so chats showed a raw "[SILENT] No new
        # messages…" pair every cron tick (2026-08-14). Hide the ENTIRE
        # turn: rows of one turn are batch-persisted with a shared
        # created_at, so dropping every row at a silent reply's timestamp
        # erases the no-news run (its [JOB:] pill and tool rows included)
        # while real, non-silent alerts keep their full turn.
        _silent_turn_ts = {
            r[5]
            for r, content in fetched
            if r[1] == "assistant" and content.startswith("[SILENT]")
        }

        messages = []
        for r, content in reversed(fetched):
            if r[5] in _silent_turn_ts:
                continue

            # Hide the synthetic brain fan-out CONSOLIDATION prompt from the
            # chat UI. task_runner._consolidate enqueues a user-role turn whose
            # content is the internal "[Background fan-out complete — N tasks
            # finished] … Write ONE consolidated summary …" instruction; it is
            # machinery, not something the user typed, and leaked into the
            # mobile/web chat as a green user bubble (2026-07-01). The brain's
            # ASSISTANT summary reply is a normal row and stays visible.
            if r[1] == "user" and is_consolidation_turn(content):
                continue

            metadata_raw = decrypt_field(r[4], key) if r[4] else None

            tool_calls = _extract_tool_calls(metadata_raw)
            if tool_calls is not None:
                tool_calls = _enrich_tool_calls(tool_calls, tool_results)

            # Assistant rows are persisted with their internal <plan>/
            # <taor_plan>/<think> blocks intact (the TAOR reasoning
            # preamble). No client renders XML — users saw a wall of raw
            # plan markup above every reply ("chat is a mess",
            # 2026-08-14). Strip at read time so ALL history heals for
            # every client; the stored row keeps the full text.
            if r[1] == "assistant" and content:
                content = _strip_internal_blocks(content)

            msg = {
                "id": r[0],
                "role": r[1],
                "content": content,
                "tool_name": r[3],
                "tool_calls": tool_calls,
                "created_at": r[5],
            }
            # Notification chat cards (chat_card leg of the spine) carry a
            # metadata marker — expose it so clients can style the row.
            # Every other row keeps its payload shape unchanged.
            if is_notification_card_metadata(metadata_raw):
                msg["kind"] = "notification"
            # Heartbeat-stamped internal turns ([JOB:/[WATCHER:/[REMINDER —
            # see heartbeat/daemon.py) — tag so clients can label/collapse
            # them. Content itself stays unchanged.
            if r[1] == "user" and content.startswith(BACKGROUND_TURN_PREFIXES):
                msg["kind"] = "cron"
            messages.append(msg)

    logger.debug(
        "[route:chat] GET session messages id=%s user=%s limit=%d paged=%s -> count=%d",
        session_id, user.id, limit, bool(before), len(messages),
    )
    return {"messages": messages}
