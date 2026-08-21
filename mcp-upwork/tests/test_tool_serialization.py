"""Whole-tool serialization (2026-08-20 22:00 shared-tab race).

The gig-pipeline's `search_jobs` and the message-watch cron's
`get_messages` ran concurrently on the ONE shared Brave tab. `_NAV_LOCK`
serializes only the NAVIGATION inside `safe_goto` — the scrape after it
runs unlocked, so the search re-navigated the tab mid-extract and
get_messages scraped the job-SEARCH page (logged as
`selector_drift_or_truly_empty url=…/search/jobs/…`; the F1 gate caught
the ungrounded reply downstream).

Fix: every `@mcp.tool()` in server.py is wrapped with `@serialized` — a
single module-level lock making each tool call's navigate+settle+scrape
span atomic. Tools never call each other (verified), so the lock cannot
deadlock; Upwork's CDN dominates wall time, so serialization costs
nothing real.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from upwork_mcp.server import serialized

_SERVER_SRC = (
    Path(__file__).resolve().parents[1] / "src" / "upwork_mcp" / "server.py"
).read_text(encoding="utf-8")


def test_concurrent_calls_never_overlap() -> None:
    state = {"active": 0, "peak": 0}

    @serialized
    async def slow(tag: str) -> str:
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.02)
        state["active"] -= 1
        return tag

    async def run():
        return await asyncio.gather(slow("a"), slow("b"), slow("c"))

    results = asyncio.run(run())
    assert results == ["a", "b", "c"]
    assert state["peak"] == 1, (
        "two tool bodies overlapped — the shared-tab race is open again"
    )


def test_serialized_preserves_signature_for_fastmcp() -> None:
    import inspect

    @serialized
    async def sample(room_id: str, limit: int = 5) -> str:
        """Docstring survives."""
        return room_id

    sig = inspect.signature(sample)
    assert list(sig.parameters) == ["room_id", "limit"]
    assert sample.__doc__ == "Docstring survives."
    assert sample.__name__ == "sample"


def test_every_registered_tool_is_serialized() -> None:
    """CI gate: each `@mcp.tool()` decorator in server.py must be
    immediately followed by `@serialized` — a NEW tool added without it
    reopens the race."""
    tool_decorators = re.findall(
        r"@mcp\.tool\(\)\s*\n(\s*@\w+\s*\n)?", _SERVER_SRC,
    )
    assert tool_decorators, "no @mcp.tool() found — server.py moved?"
    missing = sum(
        1 for follower in tool_decorators
        if "@serialized" not in (follower or "")
    )
    assert missing == 0, (
        f"{missing} tool(s) registered without @serialized — every tool "
        "call must hold the whole-tool lock"
    )
