"""Per-user 'currently running template' tracker.

Read by chat_ws + browser routes to decide whether a user message / reject
counts as a correction to append to a template's playbook. Cleared at turn
end so corrections from a non-templated next turn don't bleed in.

In-memory only (process-local). Templated runs are scoped to a single
WebSocket lifetime so persistence isn't useful — restart = clean state.
"""

from __future__ import annotations

from threading import Lock

_lock = Lock()
_active: dict[str, str] = {}


def set_active(user_id: str, tpl_id: str) -> None:
    if not user_id or not tpl_id:
        return
    with _lock:
        _active[user_id] = tpl_id


def get_active(user_id: str) -> str | None:
    if not user_id:
        return None
    with _lock:
        return _active.get(user_id)


def clear_active(user_id: str) -> None:
    if not user_id:
        return
    with _lock:
        _active.pop(user_id, None)
