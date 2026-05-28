"""Best-effort push notifications from inside skills.

Skills don't have direct access to the running Telegram bot instance,
so this helper builds a one-shot ``telegram.Bot`` from config + env.
Failures are swallowed and logged — skills should never error because
a push could not be delivered.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Sequence

logger = logging.getLogger(__name__)


async def push_telegram(
    config: Any,
    text: str,
    *,
    parse_mode: str | None = "Markdown",
    max_chars: int = 3800,
    inline_keyboard: Sequence[Sequence[dict]] | None = None,
) -> bool:
    """Send a Telegram message to the admin chat.

    Returns ``True`` if the send attempt completed without raising, ``False``
    if Telegram is not configured or any step failed. This is intentionally
    best-effort — skills should continue on failure.

    ``inline_keyboard`` is a 2D matrix of button specs, each a dict with
    ``{"text": "...", "callback_data": "..."}``. Used by the contract-
    intake watcher push to surface a one-tap ✅ Accept button alongside
    the alert text. Pass ``None`` for plain-text pushes.
    """
    token = getattr(config, "telegram_bot_token", None) if config else None
    chat_id = os.environ.get("TELEGRAM_ADMIN_CHAT")
    if not token or not chat_id:
        logger.debug("push_telegram skipped: missing token or admin chat id")
        return False

    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."

    try:
        from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup  # type: ignore
    except ImportError:
        logger.debug("push_telegram skipped: telegram package not installed")
        return False

    reply_markup = None
    if inline_keyboard:
        try:
            rows: list[list[InlineKeyboardButton]] = []
            for row in inline_keyboard:
                btn_row: list[InlineKeyboardButton] = []
                for btn in row:
                    if not isinstance(btn, dict):
                        continue
                    txt = btn.get("text")
                    cd = btn.get("callback_data")
                    if not txt or not cd:
                        continue
                    # Telegram callback_data hard cap: 64 bytes
                    cd_str = str(cd)[:64]
                    btn_row.append(InlineKeyboardButton(
                        str(txt), callback_data=cd_str,
                    ))
                if btn_row:
                    rows.append(btn_row)
            if rows:
                reply_markup = InlineKeyboardMarkup(rows)
        except Exception as exc:
            logger.warning(
                "push_telegram inline_keyboard build failed: %s", exc,
            )

    try:
        bot = Bot(token=token)
        await bot.send_message(
            chat_id=int(chat_id),
            text=text,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
        return True
    except Exception as exc:
        logger.warning("push_telegram failed: %s", exc)
        return False


async def push_telegram_document(
    config: Any,
    content: bytes,
    filename: str,
    *,
    caption: str | None = None,
) -> bool:
    """Send a file to the admin Telegram chat as a document attachment.

    Same best-effort contract as :func:`push_telegram`: returns ``True`` if the
    send completed, ``False`` if Telegram isn't configured or any step failed.
    Used by ``send_sheet`` to deliver an exported .xlsx/.csv to the user.
    """
    import io

    token = getattr(config, "telegram_bot_token", None) if config else None
    chat_id = os.environ.get("TELEGRAM_ADMIN_CHAT")
    if not token or not chat_id:
        logger.debug("push_telegram_document skipped: missing token or admin chat id")
        return False
    if not content:
        return False

    try:
        from telegram import Bot  # type: ignore
    except ImportError:
        logger.debug("push_telegram_document skipped: telegram package not installed")
        return False

    try:
        bot = Bot(token=token)
        buf = io.BytesIO(content)
        buf.name = filename
        await bot.send_document(
            chat_id=int(chat_id),
            document=buf,
            filename=filename,
            caption=(caption or "")[:1024] or None,
        )
        return True
    except Exception as exc:
        logger.warning("push_telegram_document failed: %s", exc)
        return False
