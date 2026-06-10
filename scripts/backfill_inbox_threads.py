#!/usr/bin/env python3
"""Backfill unified-inbox threads from the WhatsApp MCP's message cache.

The watcher only mirrors NEW messages into ``channel_threads`` — after the
2026-06-10 handle fix (display-name handles → stable chat JIDs) the stale
rows were soft-deleted, leaving the inbox empty until contacts write again.
This one-off seeds threads for the most recently active DIRECT chats from
``data/whatsapp_sessions/messages.json`` so the inbox is useful immediately.

Usage (host, repo root):
    python scripts/backfill_inbox_threads.py [--limit 8] [--groups]

Idempotent: upsert_thread dedups by the handle's HMAC, so re-runs just
refresh previews.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from lazyclaw.comms import thread_store
from lazyclaw.config import load_config
from lazyclaw.db.connection import close_pool
from lazyclaw.notifications.channel import resolve_admin_user_id

_CACHE = Path("data/whatsapp_sessions/messages.json")


def _body_preview(msg: dict) -> str:
    m = msg.get("message") or {}
    return (
        m.get("conversation")
        or (m.get("extendedTextMessage") or {}).get("text")
        or (m.get("imageMessage") or {}).get("caption")
        or ("[photo]" if m.get("imageMessage") else None)
        or ("[video]" if m.get("videoMessage") else None)
        or ("[voice note]" if m.get("audioMessage") else None)
        or ("[document]" if m.get("documentMessage") else None)
        or ("[sticker]" if m.get("stickerMessage") else None)
        or ""
    )[:120]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--groups", action="store_true",
                        help="include group chats (default: direct only)")
    args = parser.parse_args()

    config = load_config()
    user_id = await resolve_admin_user_id(config)
    if not user_id:
        raise SystemExit("no admin user found")

    cache = json.loads(_CACHE.read_text())
    candidates = []
    for jid, msgs in cache.items():
        if jid == "status@broadcast" or not isinstance(msgs, list) or not msgs:
            continue
        is_direct = jid.endswith("@s.whatsapp.net")
        if not is_direct and not (args.groups and jid.endswith("@g.us")):
            continue
        last = max(msgs, key=lambda m: int(m.get("messageTimestamp") or 0))
        ts = int(last.get("messageTimestamp") or 0)
        if ts <= 0:
            continue
        # Display name: last inbound pushName; fall back to the phone digits.
        name = next(
            (m.get("pushName") for m in reversed(msgs)
             if m.get("pushName") and not (m.get("key") or {}).get("fromMe")),
            None,
        ) or jid.split("@")[0]
        candidates.append((ts, jid, name, _body_preview(last)))

    candidates.sort(reverse=True)
    seeded = 0
    for _ts, jid, name, preview in candidates[: args.limit]:
        await thread_store.upsert_thread(
            config, user_id,
            channel="whatsapp",
            contact_handle=jid,
            contact_name=name,
            preview=preview or None,
            increment_unread=False,
        )
        seeded += 1
    print(f"seeded {seeded} whatsapp threads for user {user_id[:8]}…", flush=True)
    # Without this the pool's connection keeps the process alive forever.
    await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
