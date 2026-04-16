"""Best-effort push notifications from inside skills.

Skills don't have direct access to the running Telegram bot instance,
so this helper builds a one-shot ``telegram.Bot`` from config + env.
Failures are swallowed and logged — skills should never error because
a push could not be delivered.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


async def push_telegram(
    config: Any,
    text: str,
    *,
    parse_mode: str = "Markdown",
    max_chars: int = 3800,
) -> bool:
    """Send a Telegram message to the admin chat.

    Returns ``True`` if the send attempt completed without raising, ``False``
    if Telegram is not configured or any step failed. This is intentionally
    best-effort — skills should continue on failure.
    """
    token = getattr(config, "telegram_bot_token", None) if config else None
    chat_id = os.environ.get("TELEGRAM_ADMIN_CHAT")
    if not token or not chat_id:
        logger.debug("push_telegram skipped: missing token or admin chat id")
        return False

    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."

    try:
        from telegram import Bot  # type: ignore
    except ImportError:
        logger.debug("push_telegram skipped: telegram package not installed")
        return False

    try:
        bot = Bot(token=token)
        await bot.send_message(
            chat_id=int(chat_id),
            text=text,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
        )
        return True
    except Exception as exc:
        logger.warning("push_telegram failed: %s", exc)
        return False
