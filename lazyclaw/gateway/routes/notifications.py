"""In-app notification feed endpoint.

The mobile app polls this to pull server-originated notifications it hasn't
seen yet — the same delta pattern as the offline-sync ``/changes`` feeds:

  GET /api/notifications?since=<iso> → {notifications: [...], now: "<iso>"}

There is no server-side ack: the client persists ``now`` and sends it back as
``since`` on the next pull. Only notifications recorded for ``app`` / ``both``
channels land here (see ``lazyclaw.notifications.channel``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from lazyclaw.config import Config, load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.notifications.feed_store import get_notifications_since

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    since: str | None = Query(
        None,
        description=(
            "ISO-8601 timestamp — return notifications created after this. "
            "Use the `now` field from the previous response as the next "
            "`since` value. Omit for a full pull."
        ),
    ),
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Delta feed of in-app notifications for the authenticated user.

    Returns the raw ``{notifications, now}`` shape (mirrors ``/changes``) —
    NOT wrapped in a success/data envelope.
    """
    return await get_notifications_since(config, user.id, since)
