"""Tests for the Tier-A slim heartbeat path.

The slim path bypasses ``build_context()`` and the full SOUL.md when a
turn is triggered by an announce-style heartbeat (REMINDER / WATCHER /
MCP_WATCHER / TASK_REMINDER:<id>). [JOB:...] is intentionally excluded —
that prefix can carry arbitrary work and stays on the full path.

These tests pin the regex coverage and the personality file so a future
edit can't silently re-route every reminder back through the heavy
context build.
"""

from __future__ import annotations

import pytest

from lazyclaw.runtime.agent import (
    _HEARTBEAT_TELEMETRY_RE,
    _SLIM_HEARTBEAT_PREFIX_RE,
    _TASK_REMINDER_RE,
)
from lazyclaw.runtime.personality import (
    load_heartbeat_personality,
    load_personality,
)


# ──────────────────────────────────────────────────────────────────
# Regex coverage
# ──────────────────────────────────────────────────────────────────

# (message, expect_slim_match, expect_telemetry_match)
SLIM_CASES = [
    ("[REMINDER] go to bed", True, True),
    ("[WATCHER] price changed", True, True),
    ("[MCP_WATCHER] new email", True, True),
    ("[TASK_REMINDER:abc-123] pay bill", True, True),
    ("[TASK_REMINDER:7f3e4d2a-1b5c-4e6f] x", True, True),
]

# Tier B: telemetry yes, slim no.
TIER_B_CASES = [
    ("[JOB:scrape-prices] run nightly", False, True),
    ("[JOB:morning-brief]", False, True),
]

# Plain chat — neither regex should match.
NON_HEARTBEAT_CASES = [
    "hi how are you",
    "show me the weather",
    "please handle this [TASK_REMINDER:abc] later",  # not at start
    "",
]


@pytest.mark.parametrize("message,expect_slim,expect_telemetry", SLIM_CASES)
def test_tier_a_matches_slim_and_telemetry(
    message: str, expect_slim: bool, expect_telemetry: bool
) -> None:
    assert bool(_SLIM_HEARTBEAT_PREFIX_RE.match(message)) is expect_slim
    assert bool(_HEARTBEAT_TELEMETRY_RE.match(message)) is expect_telemetry


@pytest.mark.parametrize("message,expect_slim,expect_telemetry", TIER_B_CASES)
def test_tier_b_skipped_by_slim_kept_by_telemetry(
    message: str, expect_slim: bool, expect_telemetry: bool
) -> None:
    """[JOB:...] must NOT trigger the slim path (preserves current behavior
    for arbitrary work) but MUST be visible to telemetry."""
    assert bool(_SLIM_HEARTBEAT_PREFIX_RE.match(message)) is expect_slim
    assert bool(_HEARTBEAT_TELEMETRY_RE.match(message)) is expect_telemetry


@pytest.mark.parametrize("message", NON_HEARTBEAT_CASES)
def test_plain_chat_matches_neither_regex(message: str) -> None:
    assert _SLIM_HEARTBEAT_PREFIX_RE.match(message) is None
    assert _HEARTBEAT_TELEMETRY_RE.match(message) is None


def test_existing_task_reminder_regex_unchanged() -> None:
    """The slim-path work must NOT have weakened _TASK_REMINDER_RE — that
    regex feeds the bound-task safety net in the finally block."""
    m = _TASK_REMINDER_RE.match("[TASK_REMINDER:abc123] do the thing")
    assert m is not None
    assert m.group(1) == "abc123"


# ──────────────────────────────────────────────────────────────────
# Personality loading
# ──────────────────────────────────────────────────────────────────


def test_heartbeat_personality_is_substantially_smaller_than_soul() -> None:
    """Slim path is only worthwhile if HEARTBEAT.md is dramatically smaller
    than SOUL.md. Pin the size ratio so a future SOUL.md trim or HEARTBEAT.md
    growth doesn't quietly erase the win."""
    soul = load_personality()
    hb = load_heartbeat_personality()

    assert len(hb) > 200, "HEARTBEAT.md is suspiciously small — file missing?"
    assert len(soul) > 5_000, "SOUL.md is suspiciously small — file moved?"

    # Heartbeat must be at least 5x smaller than SOUL.
    assert len(hb) * 5 < len(soul), (
        f"HEARTBEAT.md ({len(hb)}) is not <20% of SOUL.md ({len(soul)}). "
        "Slim path savings have eroded — review HEARTBEAT.md size."
    )


def test_heartbeat_personality_caches_across_calls() -> None:
    """Same content + same mtime → cached in-memory; second call hits cache."""
    a = load_heartbeat_personality()
    b = load_heartbeat_personality()
    assert a is b  # exact object identity confirms cache hit


def test_heartbeat_personality_mentions_required_concepts() -> None:
    """If the HEARTBEAT.md content drifts away from these anchors, the agent
    will misbehave on real heartbeat traffic. Pin the core contract."""
    hb = load_heartbeat_personality().lower()
    for required in (
        "lazyclaw",
        "[reminder]",
        "[task_reminder",
        "[watcher]",
        "search_tools",
        "complete_task",
    ):
        assert required in hb, f"HEARTBEAT.md missing key concept: {required!r}"
