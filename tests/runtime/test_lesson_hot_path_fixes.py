"""Lesson recorder hot-path fixes (2026-08-21 learning-machinery audit).

Measured: 173 recorder dispatches/day AWAITED INLINE in tool_executor
(docstring said "never blocks" — false as wired), one card re-upserted
926 times with no unchanged-content skip (925 pure churn writes through
the single shared aiosqlite connection), and a DUPLICATE inline save in
mcp/bridge.py giving whatsapp/email/instagram calls two full upsert
cycles into two different cards.

Fixes: recorder truly fire-and-forget; same-outcome replays throttled
via should_skip_replay; bridge duplicate deleted (tool_executor already
records every call).
"""

from __future__ import annotations

import inspect
import time

from lazyclaw.runtime import skill_lesson as sl
from lazyclaw.runtime.skill_lesson import should_skip_replay


def _iso(ts: float) -> str:
    import datetime as dt

    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat()


NOW = time.time()


def test_same_outcome_recent_replay_is_skipped() -> None:
    assert should_skip_replay(
        "pending", "pending", _iso(NOW - 60), NOW,
    ) is True


def test_outcome_change_is_never_skipped() -> None:
    assert should_skip_replay(
        "verified", "failed", _iso(NOW - 60), NOW,
    ) is False
    assert should_skip_replay(
        "pending", "success", _iso(NOW - 60), NOW,
    ) is False


def test_stale_replay_writes_again() -> None:
    assert should_skip_replay(
        "pending", "pending", _iso(NOW - 2 * 3600), NOW,
    ) is False


def test_missing_or_bad_timestamp_writes() -> None:
    assert should_skip_replay("pending", "pending", "", NOW) is False
    assert should_skip_replay("pending", "pending", None, NOW) is False
    assert should_skip_replay("pending", "pending", "not-a-date", NOW) is False


def test_save_consults_the_skip_before_updating() -> None:
    src = inspect.getsource(sl.save_skill_lesson)
    skip_idx = src.index("should_skip_replay(")
    update_idx = src.index("update_note")
    assert skip_idx < update_idx, (
        "the throttle must run before the read-decrypt-merge-re-encrypt "
        "cycle, or the 926x churn continues"
    )


def test_recorder_is_fire_and_forget_in_tool_executor() -> None:
    import lazyclaw.runtime.tool_executor as te

    src = inspect.getsource(te)
    assert "await record_skill_outcome(" not in src, (
        "the recorder must not be awaited inline in the tool hot path"
    )
    assert "record_skill_outcome(" in src, "recording must still happen"
    assert "fire_and_forget(" in src


def test_bridge_duplicate_save_deleted() -> None:
    import lazyclaw.mcp.bridge as bridge

    src = inspect.getsource(bridge)
    assert "save_skill_lesson" not in src, (
        "tool_executor already records every MCP call — the bridge's "
        "inline save double-charged whatsapp/email/instagram calls and "
        "fragmented cards"
    )
