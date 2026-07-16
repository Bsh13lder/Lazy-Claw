"""Per-user toggle: stream background-task progress into Web UI chat?

When ``bg_streaming`` is True (default), the chat_ws layer surfaces
``bg_event`` / ``bg_tool_call`` / ``bg_tool_result`` / ``bg_token``
frames from background tasks into the active chat — the user sees live
progress under a "Background: X" card.

When False, those frames are dropped and the user only sees the final
``background_done`` / ``background_failed`` result card. Quieter chat,
brain stays free to handle the next message.

Lives in ``users.settings`` JSON under the ``bg_streaming`` key. Toggle
via ``/streaming on|off|status`` chat command or the
``/api/agent/streaming/settings`` endpoint.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

_SETTING_KEY = "bg_streaming"


async def get_bg_streaming(config: Any, user_id: str) -> bool:
    """Read the user's bg_streaming setting. Defaults to True (stream on)."""
    if not user_id:
        return True
    try:
        async with db_session(config) as db:
            cursor = await db.execute(
                "SELECT settings FROM users WHERE id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
    except Exception:
        logger.debug(
            "[stream] get_bg_streaming db read failed user=%s",
            user_id[:8] if user_id else None, exc_info=True,
        )
        return True
    if not row or not row[0]:
        return True
    try:
        settings = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        logger.debug(
            "[stream] get_bg_streaming settings JSON parse failed user=%s, "
            "defaulting to on", user_id[:8] if user_id else None, exc_info=True,
        )
        return True
    value = settings.get(_SETTING_KEY)
    if value is None:
        return True
    return bool(value)


async def set_bg_streaming(
    config: Any, user_id: str, enabled: bool,
) -> bool:
    """Update the user's bg_streaming setting. Returns the persisted value."""
    if not user_id:
        return True
    flag = bool(enabled)
    try:
        async with db_session(config) as db:
            cursor = await db.execute(
                "SELECT settings FROM users WHERE id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
            settings: dict = {}
            if row and row[0]:
                try:
                    settings = json.loads(row[0]) or {}
                except (TypeError, json.JSONDecodeError):
                    logger.debug(
                        "[stream] set_bg_streaming existing settings JSON "
                        "parse failed user=%s, starting fresh",
                        user_id[:8], exc_info=True,
                    )
                    settings = {}
            settings[_SETTING_KEY] = flag
            await db.execute(
                "UPDATE users SET settings = ? WHERE id = ?",
                (json.dumps(settings), user_id),
            )
            await db.commit()
        logger.debug(
            "[stream] set_bg_streaming applied key=%s user=%s",
            _SETTING_KEY, user_id[:8],
        )
    except Exception:
        logger.warning(
            "[stream] set_bg_streaming db write failed user=%s",
            user_id[:8], exc_info=True,
        )
        return await get_bg_streaming(config, user_id)
    return flag


def parse_streaming_command(message: str) -> tuple[str, bool | None] | None:
    """Parse ``/streaming on|off|status`` (and synonyms).

    Returns ``(action, enabled)`` where action is one of
    ``"set"`` (enabled is True/False) or ``"status"`` (enabled is None).
    Returns None when the message isn't a streaming command.

    Pure function — no DB access; easy to unit-test.
    """
    if not message:
        return None
    text = message.strip().lower()
    if not text.startswith("/streaming"):
        return None
    rest = text[len("/streaming"):].strip()
    if not rest or rest in ("status", "?"):
        return ("status", None)
    if rest in ("on", "1", "true", "verbose", "yes"):
        return ("set", True)
    if rest in ("off", "0", "false", "silent", "quiet", "no"):
        return ("set", False)
    return None
