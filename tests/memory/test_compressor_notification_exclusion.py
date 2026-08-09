"""Context-pollution guard — notification chat cards NEVER enter LLM context.

The chat_card leg of the notification spine writes assistant-role rows into
``agent_messages`` (so pings render in the app chat). This project has a
documented history-pollution incident class: an assistant row the brain
re-reads as "something I previously said" gets mimicked and re-taught on
every later turn. Marked rows must therefore be excluded by
``compress_history`` on BOTH paths (fast ≤WINDOW and full >WINDOW), before
any summarization can bake them into long-term context.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import encrypt
from lazyclaw.crypto.key_manager import get_user_dek
from lazyclaw.db.connection import close_pool, db_session, init_db
from lazyclaw.memory.chat_message_store import build_notification_metadata
from lazyclaw.memory.compressor import WINDOW_SIZE, compress_history
from lazyclaw.memory.metadata_codec import encode_tool_metadata

pytestmark = pytest.mark.asyncio

_PING = "⏰ Medicine\nTake your dose now"


@pytest.fixture
async def cfg(tmp_path: Path):
    c = Config(database_dir=tmp_path)
    await init_db(c)
    async with db_session(c) as db:
        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt) "
            "VALUES (?, ?, ?, ?)",
            ("u1", "alice", "x", "salt-a"),
        )
        await db.commit()
    try:
        yield c
    finally:
        await close_pool()


@pytest.fixture(autouse=True)
def _mute_daily_summary_trigger(monkeypatch):
    """compress_history fires a background daily-summary check — stub the
    log lookup so no fire-and-forget LLM task spawns in tests."""
    monkeypatch.setattr(
        "lazyclaw.memory.daily_log.get_daily_log",
        AsyncMock(return_value={"summary": "already summarized"}),
    )


async def _rows(cfg, n_turns: int, notification_positions: set[int]):
    """Build raw agent_messages-shaped tuples: alternating user/assistant
    turns with notification-card rows injected at the given indices."""
    key = await get_user_dek(cfg, "u1")
    rows = []
    for i in range(n_turns):
        if i in notification_positions:
            rows.append((
                f"m{i}", "assistant", encrypt(_PING, key), None,
                encode_tool_metadata(
                    build_notification_metadata("task_reminder"), key,
                ),
            ))
        elif i % 2 == 0:
            rows.append((f"m{i}", "user", encrypt(f"question {i}", key), None, None))
        else:
            rows.append((f"m{i}", "assistant", encrypt(f"answer {i}", key), None, None))
    return rows


async def test_fast_path_excludes_notification_rows(cfg):
    raw = await _rows(cfg, 6, notification_positions={2, 5})
    history = await compress_history(cfg, None, "u1", "s1", raw)

    contents = [m.content for m in history]
    assert all(_PING not in c for c in contents), (
        "notification card leaked into LLM context (fast path)"
    )
    assert len(history) == 4
    assert "question 0" in contents[0]


async def test_full_path_excludes_notification_rows_before_summary_split(cfg):
    # > WINDOW_SIZE raw rows forces the full path; after exclusion the
    # survivors fit the window again, so no summarizer/LLM is needed —
    # which itself proves the exclusion runs BEFORE the split.
    n = WINDOW_SIZE + 5
    notif_positions = set(range(0, 10))
    raw = await _rows(cfg, n, notification_positions=notif_positions)
    history = await compress_history(cfg, None, "u1", "s1", raw)

    contents = [m.content for m in history]
    assert all(_PING not in c for c in contents), (
        "notification card leaked into LLM context (full path)"
    )
    assert all("Medicine" not in c for c in contents)
    assert len(history) == n - len(notif_positions)


async def test_normal_rows_unaffected(cfg):
    """Byte-identical behavior for turns without notification rows."""
    raw = await _rows(cfg, 6, notification_positions=set())
    history = await compress_history(cfg, None, "u1", "s1", raw)
    assert [m.role for m in history] == [
        "user", "assistant", "user", "assistant", "user", "assistant",
    ]
    assert history[0].content == "question 0"
    assert history[1].content == "answer 1"


async def test_tool_call_metadata_rows_still_reconstructed(cfg):
    """Tool-call metadata (a JSON list) must not be mistaken for the
    notification marker (a JSON dict)."""
    import json

    key = await get_user_dek(cfg, "u1")
    tc_meta = encode_tool_metadata(
        json.dumps([{"id": "tc1", "name": "web_search", "arguments": {}}]), key,
    )
    raw = [
        ("m0", "user", encrypt("search something", key), None, None),
        ("m1", "assistant", encrypt("", key), None, tc_meta),
        ("m2", "tool", encrypt("result payload", key), "tc1", None),
        ("m3", "assistant", encrypt("here you go", key), None, None),
    ]
    history = await compress_history(cfg, None, "u1", "s1", raw)
    roles = [m.role for m in history]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert history[1].tool_calls and history[1].tool_calls[0].name == "web_search"
