"""Delete stuck Google OAuth memory entry that shouldn't have been saved.
Content is not printed — only counts."""
from __future__ import annotations

import asyncio
import sys

from lazyclaw.config import load_config
from lazyclaw.memory.personal import get_memories, delete_memory


async def main() -> None:
    user_id = sys.argv[1] if len(sys.argv) > 1 else None
    if not user_id:
        print("usage: clean_oauth_memory.py <user_id>")
        sys.exit(2)

    config = load_config()
    mems = await get_memories(config, user_id, limit=500)
    print(f"scanning {len(mems)} memories")

    markers = (
        "gocspx",
        "googleusercontent.com",
        "oauth_client",
        "oauth client",
        "client_secret",
    )
    targets = []
    for m in mems:
        content = (m.get("content") or "").lower()
        if any(mark in content for mark in markers):
            targets.append(m["id"])

    print(f"matched {len(targets)} OAuth-like entries")
    deleted = 0
    for mid in targets:
        ok = await delete_memory(config, user_id, mid)
        if ok:
            deleted += 1
    print(f"deleted {deleted}")


asyncio.run(main())
