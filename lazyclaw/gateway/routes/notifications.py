"""In-app notification feed endpoint (the Notification Center backend).

The mobile/web apps poll this to pull server-originated notifications they
haven't seen yet — the same delta pattern as the offline-sync ``/changes``
feeds:

  GET  /api/notifications?since=<iso>  → {notifications: [...], unread, now}
  POST /api/notifications/read         {ids:[...]} → {marked}
  POST /api/notifications/read-all     → {marked}
  GET  /api/notifications/unread-count → {unread}

The client persists ``now`` and sends it back as ``since`` on the next pull.
Read-state is tracked server-side (``read_at``) so the unread badge and "mark
read" survive a reinstall. Every discrete event is recorded here regardless of
the telegram/app/both toggle (see ``lazyclaw.notifications.spine``).
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query

from lazyclaw.config import Config, load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.notifications.feed_store import (
    get_notifications_since,
    get_unread_count,
    mark_all_read,
    mark_read,
)

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

    Returns the raw ``{notifications, unread, now}`` shape (mirrors ``/changes``)
    — NOT wrapped in a success/data envelope.
    """
    return await get_notifications_since(config, user.id, since)


@router.get("/unread-count")
async def unread_count(
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Current unread notification count (for the tab badge)."""
    return {"unread": await get_unread_count(config, user.id)}


@router.post("/read")
async def read_notifications(
    ids: list[str] = Body(..., embed=True, description="Notification ids to mark read."),
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Mark specific notifications read (scoped to the caller)."""
    marked = await mark_read(config, user.id, ids)
    return {"marked": marked}


@router.post("/read-all")
async def read_all_notifications(
    user: User = Depends(get_current_user),
    config: Config = Depends(load_config),
):
    """Mark every unread notification for the caller read."""
    marked = await mark_all_read(config, user.id)
    return {"marked": marked}
