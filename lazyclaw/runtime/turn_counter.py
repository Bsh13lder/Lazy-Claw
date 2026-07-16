"""Per-user monotonic agent-turn counter.

Used by the lesson verification pump to know how many quiet turns have
elapsed since a shape was recorded as ``pending``. After
``QUIET_TURNS_FOR_VERIFY`` (default 3) clean turns, the pump promotes
``pending → verified``.

In-process state by design — daemon restart resets all counters to 0
and every existing pending shape just waits 3 fresh turns before
auto-promotion. No data loss, no migration.
"""
from __future__ import annotations

import logging
from threading import Lock

logger = logging.getLogger(__name__)

QUIET_TURNS_FOR_VERIFY = 3

_lock = Lock()
_turns: dict[str, int] = {}


def current_turn(user_id: str) -> int:
    """Return the current turn for ``user_id`` (0 if never advanced)."""
    return _turns.get(user_id, 0)


def advance_turn(user_id: str) -> int:
    """Bump the user's turn counter by 1. Called once per agent loop
    iteration at the top, after the user message has been received but
    before tools fire.
    """
    with _lock:
        new_value = _turns.get(user_id, 0) + 1
        _turns[user_id] = new_value
        logger.debug(
            "[turncount] advance user=%s turn=%d",
            user_id[:8] if user_id else None, new_value,
        )
        return new_value


def reset_for_test() -> None:
    """Test-only — wipe every counter. Production code never calls this."""
    with _lock:
        logger.warning(
            "[turncount] reset_for_test called — clearing %d counter(s) "
            "(should only happen in tests)", len(_turns),
        )
        _turns.clear()


__all__ = [
    "QUIET_TURNS_FOR_VERIFY",
    "current_turn",
    "advance_turn",
    "reset_for_test",
]
