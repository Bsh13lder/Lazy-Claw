"""Encrypted free-form canvas boards for LazyBrain.

A canvas is an Obsidian-Canvas-style spatial workspace: note cards, free-form
text, and arrows. Persistence granularity is one encrypted JSON blob per
board — simpler than row-per-node and easier to restore atomically.

Schema: ``canvas_boards(id, user_id, name, payload, created_at, updated_at)``.
``payload`` is AES-256-GCM ciphertext over the React-Flow-shaped dict
``{nodes: [...], edges: [...], viewport: {...}}``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import decrypt_field, encrypt_field, user_aad
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)


def _canvas_aad(user_id: str) -> bytes:
    return user_aad(user_id, "canvas:payload")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _empty_payload() -> dict[str, Any]:
    return {"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}}


# Fields on a React Flow node's `data` dict that carry user-readable text.
# Order matters: first hit wins, so the most descriptive field comes first.
_TEXT_FIELDS = ("text", "content", "body", "label", "title", "noteTitle", "name")


def _extract_canvas_text(payload: dict[str, Any]) -> list[str]:
    """Walk a canvas payload and collect text-bearing strings.

    Pure function — no I/O. Returns the list of distinct text snippets
    found across all node ``data`` dicts, plus any note-reference
    ``noteTitle`` so wikilinks paint as ``[[Title]]`` for downstream
    embedding signal.
    """
    out: list[str] = []
    seen: set[str] = set()
    nodes = (payload or {}).get("nodes") or []
    if not isinstance(nodes, list):
        return out
    for node in nodes:
        if not isinstance(node, dict):
            continue
        data = node.get("data")
        if not isinstance(data, dict):
            continue
        # Note-reference: prefer the wikilink form so embeddings see the
        # title in its canonical [[…]] shape (matches LazyBrain prose).
        if data.get("noteId") and data.get("noteTitle"):
            snippet = f"[[{str(data['noteTitle']).strip()}]]"
            if snippet not in seen:
                seen.add(snippet)
                out.append(snippet)
            continue
        for field in _TEXT_FIELDS:
            v = data.get(field)
            if not isinstance(v, str):
                continue
            stripped = v.strip()
            if not stripped or stripped in seen:
                continue
            seen.add(stripped)
            out.append(stripped)
            break  # one snippet per node — avoids duplicating label+text
    return out


async def _mirror_canvas_note(
    config: Config,
    user_id: str,
    board_id: str,
    name: str,
    payload: dict[str, Any],
) -> None:
    """Mirror a canvas board's text content into LazyBrain so semantic
    search reaches it (text trapped in encrypted React-Flow JSON is
    otherwise invisible to embeddings + word search).

    Idempotent via ``find_by_title``. Skips boards with zero
    text-bearing nodes — pure shape/image canvases produce no useful
    embedding signal. Silent on failure.
    """
    try:
        snippets = _extract_canvas_text(payload)
        if not snippets:
            return  # nothing searchable on this board

        from lazyclaw.lazybrain import events as lb_events
        from lazyclaw.lazybrain import store as lb_store

        # Cap body so a 1000-node board doesn't bloat the vault.
        bullets = "\n".join(f"- {s}" for s in snippets[:200])
        if len(bullets) > 4000:
            bullets = bullets[:4000] + "\n…(truncated)"
        body = f"**Canvas:** {name}\n\n{bullets}"
        note_title = f"Canvas: {(name or 'Untitled')[:60]}"
        tags = [
            "canvas-board", "auto", "owner/user",
            f"canvas/{board_id}",
        ]

        existing = await lb_store.find_by_title(config, user_id, note_title)
        if existing:
            updated = await lb_store.update_note(
                config, user_id, existing["id"],
                content=body, tags=tags,
            )
            if updated:
                lb_events.publish_note_saved(
                    user_id, updated["id"], updated.get("title"),
                    updated.get("tags"), source="canvas-board",
                )
            return

        note = await lb_store.save_note(
            config, user_id,
            content=body, title=note_title, tags=tags, importance=4,
        )
        lb_events.publish_note_saved(
            user_id, note["id"], note["title"], note["tags"],
            source="canvas-board",
        )
    except Exception:
        logger.warning(
            "lazybrain canvas mirror failed for user %s board %s",
            user_id, board_id, exc_info=True,
        )


async def list_boards(config: Config, user_id: str) -> list[dict[str, Any]]:
    """Return a plaintext index: id, name, timestamps (no payload)."""
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name, created_at, updated_at FROM canvas_boards "
            "WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        )
        data = await rows.fetchall()
    return [
        {"id": r[0], "name": r[1], "created_at": r[2], "updated_at": r[3]}
        for r in data
    ]


async def get_board(
    config: Config, user_id: str, board_id: str
) -> dict[str, Any] | None:
    """Fetch + decrypt one board."""
    dek = await get_user_dek(config, user_id)
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, name, payload, created_at, updated_at "
            "FROM canvas_boards WHERE id = ? AND user_id = ?",
            (board_id, user_id),
        )
        row = await rows.fetchone()
    if not row:
        return None

    raw = decrypt_field(row[2], dek, _canvas_aad(user_id), fallback="")
    try:
        payload = json.loads(raw) if raw else _empty_payload()
    except json.JSONDecodeError:
        payload = _empty_payload()
    return {
        "id": row[0],
        "name": row[1],
        "payload": payload,
        "created_at": row[3],
        "updated_at": row[4],
    }


async def save_board(
    config: Config,
    user_id: str,
    name: str,
    payload: dict[str, Any],
    board_id: str | None = None,
) -> dict[str, Any]:
    """Upsert a board. Returns the index row (no payload)."""
    dek = await get_user_dek(config, user_id)
    enc = encrypt_field(json.dumps(payload), dek, _canvas_aad(user_id))
    now = _now()
    name = (name or "").strip() or "Untitled canvas"

    if board_id is None:
        board_id = str(uuid4())
        async with db_session(config) as db:
            await db.execute(
                "INSERT INTO canvas_boards (id, user_id, name, payload, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (board_id, user_id, name[:120], enc, now, now),
            )
            await db.commit()
        await _mirror_canvas_note(config, user_id, board_id, name, payload)
        return {"id": board_id, "name": name, "created_at": now, "updated_at": now}

    async with db_session(config) as db:
        cur = await db.execute(
            "UPDATE canvas_boards SET name = ?, payload = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (name[:120], enc, now, board_id, user_id),
        )
        if cur.rowcount == 0:
            # Caller passed a board_id that doesn't exist → create it
            await db.execute(
                "INSERT INTO canvas_boards (id, user_id, name, payload, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (board_id, user_id, name[:120], enc, now, now),
            )
        await db.commit()
    await _mirror_canvas_note(config, user_id, board_id, name, payload)
    return {"id": board_id, "name": name, "updated_at": now}


async def delete_board(config: Config, user_id: str, board_id: str) -> bool:
    async with db_session(config) as db:
        cur = await db.execute(
            "DELETE FROM canvas_boards WHERE id = ? AND user_id = ?",
            (board_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0
