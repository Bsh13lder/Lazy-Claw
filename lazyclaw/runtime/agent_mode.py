"""Operating modes (ADR-0005 Phase 3) — the agent's posture over permissions.

Four postures, layered OVER the per-skill allow/ask/deny system — they do NOT
replace it. The default is ASK, which maps to the historical behavior, so
existing users see no change until they pick another mode.

  CHAT  — conversation only; no tools execute.
  ASK   — act, but every write still goes through the normal allow/ask/deny gate.
  PLAN  — read-only tools run; writes are gated (forced to ASK) pending Execute.
  AUTO  — autonomous; anything that would 'ask' is auto-allowed.

Stored at ``users.settings.general.agent_mode`` and read everywhere (Telegram
``/act``, web + mobile mode switch) via the same general-settings mechanism.
The posture is applied at exactly one place — ``PermissionChecker.check_effective``
— so the brain's 2,600-line hot path is untouched.
"""
from __future__ import annotations

import enum
import logging
import time

from lazyclaw.config import Config
from lazyclaw.permissions.models import ALLOW, ASK, DENY, ResolvedPermission

logger = logging.getLogger(__name__)


class AgentMode(str, enum.Enum):
    CHAT = "chat"
    ASK = "ask"
    PLAN = "plan"
    AUTO = "auto"


DEFAULT_MODE: AgentMode = AgentMode.ASK

# UI-facing labels (the "Execute" label maps to AUTO per the founder's spec).
MODE_LABELS: dict[AgentMode, str] = {
    AgentMode.CHAT: "Chat",
    AgentMode.ASK: "Ask",
    AgentMode.PLAN: "Plan",
    AgentMode.AUTO: "Execute",
}

VALID_AGENT_MODES: frozenset[str] = frozenset(m.value for m in AgentMode)


def parse_mode(value: str | None) -> AgentMode:
    """Coerce a stored/typed string to an AgentMode, defaulting to ASK."""
    if not value:
        return DEFAULT_MODE
    try:
        return AgentMode(str(value).strip().lower())
    except ValueError:
        return DEFAULT_MODE


# Read-only classification — drives PLAN posture (read-only runs, writes gate).
# Conservative: anything not recognised is treated as a WRITE, so PLAN gates it.
_READONLY_NAMES: frozenset[str] = frozenset(
    {"web_search", "search_tools", "recall_memories", "recall_typed_memory"}
)
_READONLY_PREFIXES: tuple[str, ...] = (
    "list_", "get_", "read_", "search_", "recall_", "find_",
    "view_", "show_", "check_", "lookup_", "fetch_",
)
_READONLY_CATEGORIES: frozenset[str] = frozenset({"research"})


def is_readonly_skill(skill_name: str, category: str | None = None) -> bool:
    """Best-effort read-only classification for PLAN-mode gating."""
    name = (skill_name or "").lower()
    if name in _READONLY_NAMES:
        return True
    if any(name.startswith(p) or f"_{p}" in name for p in _READONLY_PREFIXES):
        return True
    if category and category in _READONLY_CATEGORIES:
        return True
    return False


def apply_mode_posture(
    mode: AgentMode,
    resolved: ResolvedPermission,
    *,
    is_readonly: bool,
) -> ResolvedPermission:
    """Layer the operating-mode posture over a base permission decision.

    Returns ``resolved`` unchanged for ASK (the default → zero behavior change)
    and whenever the posture would not alter the level.
    """
    level = resolved.level
    if mode is AgentMode.CHAT:
        new_level = DENY
    elif mode is AgentMode.AUTO:
        new_level = ALLOW if level == ASK else level
    elif mode is AgentMode.PLAN:
        if is_readonly:
            new_level = level
        else:
            new_level = level if level == DENY else ASK
    else:  # ASK — status quo
        new_level = level

    if new_level == level:
        return resolved
    return ResolvedPermission(
        skill_name=resolved.skill_name,
        level=new_level,
        source=f"mode:{mode.value}",
    )


# ── Cached reader (general-settings is not itself cached) ──────────────

_MODE_CACHE: dict[str, tuple[float, AgentMode]] = {}
_MODE_TTL_SECONDS: float = 5.0


async def get_agent_mode(config: Config, user_id: str) -> AgentMode:
    """Read the user's operating mode (5s TTL cache; never raises)."""
    now = time.monotonic()
    cached = _MODE_CACHE.get(user_id)
    if cached is not None and (now - cached[0]) < _MODE_TTL_SECONDS:
        return cached[1]
    try:
        from lazyclaw.settings.general import get_general_settings

        general = await get_general_settings(config, user_id)
        mode = parse_mode(general.get("agent_mode"))
    except Exception:
        logger.debug("get_agent_mode failed; defaulting", exc_info=True)
        mode = DEFAULT_MODE
    _MODE_CACHE[user_id] = (now, mode)
    return mode


def invalidate_mode_cache(user_id: str | None = None) -> None:
    """Drop the cached mode so a settings change takes effect immediately."""
    if user_id is None:
        _MODE_CACHE.clear()
    else:
        _MODE_CACHE.pop(user_id, None)


__all__ = [
    "AgentMode",
    "DEFAULT_MODE",
    "MODE_LABELS",
    "VALID_AGENT_MODES",
    "parse_mode",
    "is_readonly_skill",
    "apply_mode_posture",
    "get_agent_mode",
    "invalidate_mode_cache",
]
