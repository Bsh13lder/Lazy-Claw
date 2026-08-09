"""Shared app-side fan-out for the legacy funnels — chat card + WS frame.

The spine (:mod:`lazyclaw.notifications.spine`) wires its own two transport
legs; the three legacy funnels (``push.push_telegram``,
``heartbeat_push.deliver_heartbeat_push``,
``telegram_notifier.TelegramNotifier``) share this one helper so the
"drop the ping into chat + tell an open app" pair isn't re-implemented
three times.

Fail-open: both legs are best-effort and this helper NEVER raises into the
ping path.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def fan_out_app_ping(
    config: Any,
    user_id: str,
    notif: dict,
    *,
    chat: bool = True,
    realtime: bool = True,
) -> None:
    """Deliver one notification dict to the app transports.

    ``notif`` is the decrypted feed-row dict shape
    (``{id, kind, title, body, created_at, ...}``). ``chat``/``realtime``
    let a caller drop one leg (Class-A live hints publish the frame only —
    their chat row already exists via the agent turn's own persistence).
    """
    if not user_id or not isinstance(notif, dict):
        return
    if chat:
        try:
            from lazyclaw.notifications import chat_card

            await chat_card.emit(config, user_id, notif)
        except Exception:
            logger.debug("app fan-out: chat card leg failed", exc_info=True)
    if realtime:
        try:
            from lazyclaw.notifications import realtime as realtime_bus

            await realtime_bus.emit(config, user_id, notif)
        except Exception:
            logger.debug("app fan-out: realtime leg failed", exc_info=True)
