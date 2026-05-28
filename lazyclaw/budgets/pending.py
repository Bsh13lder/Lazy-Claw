"""TTL cache for pending expense-choice prompts.

When ``add_expense`` can't unambiguously resolve a project (or task within
a project), it stashes the in-flight amount/description plus the candidate
list here, keyed by ``user_id``. The Telegram channel reads the entry on
its next outbound reply and attaches an inline-button keyboard so the user
picks with one tap instead of typing the name back.

Non-Telegram channels (Web UI chat, CLI) ignore the cache — the skill's
text response already lists candidates inline, and the entry expires after
5 minutes harmlessly.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Literal

_TTL_SECONDS = 300  # 5 min — long enough for typed clarification, short enough that stale state doesn't haunt a session.


@dataclass(frozen=True)
class PendingExpenseChoice:
    user_id: str
    amount: float
    currency: str | None
    description: str | None
    vendor: str | None
    spent_at: str | None
    task_name: str | None
    kind: Literal["project", "task"]
    # (id, human label) pairs; ``id`` is project_id or task_id depending on kind.
    candidates: tuple[tuple[str, str], ...]
    project_id: str | None       # set when kind == "task" (the parent project)
    project_name: str | None
    token: str
    created_at: float = field(default_factory=time.time)


_PENDING: dict[str, PendingExpenseChoice] = {}


def new_token() -> str:
    return secrets.token_hex(6)


def set_pending(entry: PendingExpenseChoice) -> str:
    """Stash a pending choice. Overwrites any prior entry for this user
    (the most recent ambiguous capture wins)."""
    _PENDING[entry.user_id] = entry
    return entry.token


def get_pending(user_id: str) -> PendingExpenseChoice | None:
    """Return the user's pending choice if it exists and hasn't expired,
    else None. Side effect: prunes the entry on expiry."""
    entry = _PENDING.get(user_id)
    if entry is None:
        return None
    if time.time() - entry.created_at > _TTL_SECONDS:
        _PENDING.pop(user_id, None)
        return None
    return entry


def clear_pending(user_id: str) -> None:
    _PENDING.pop(user_id, None)
