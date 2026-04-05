"""Webhook endpoint for external services (n8n, Stripe, etc.) to push events.

n8n workflows can call POST /api/webhook with a message and optional
metadata. LazyClaw processes it as if the admin sent a Telegram message,
and pushes the response back to Telegram.

Auth: uses a shared webhook secret (vault key 'webhook_secret' or env WEBHOOK_SECRET).
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from lazyclaw.config import load_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhook", tags=["webhook"])

_config = load_config()

# Injected by app.py
_lane_queue = None
_default_user_id: str | None = None


def set_webhook_deps(lane_queue, default_user_id: str | None = None) -> None:
    """Called by cli.py/app.py to inject dependencies."""
    global _lane_queue, _default_user_id
    _lane_queue = lane_queue
    _default_user_id = default_user_id


class WebhookRequest(BaseModel):
    message: str
    source: str = "n8n"
    metadata: dict | None = None


class WebhookResponse(BaseModel):
    status: str
    response: str | None = None


async def _verify_secret(request: Request) -> None:
    """Check webhook secret from header or query param."""
    expected = os.getenv("WEBHOOK_SECRET", "")
    if not expected:
        # Try vault
        try:
            from lazyclaw.crypto.vault import get_credential
            if _default_user_id:
                expected = await get_credential(_config, _default_user_id, "webhook_secret") or ""
        except Exception:
            pass

    if not expected:
        # No secret configured = open (local Docker network only)
        return

    provided = (
        request.headers.get("X-Webhook-Secret", "")
        or request.query_params.get("secret", "")
    )
    if provided != expected:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")


@router.post("")
async def receive_webhook(body: WebhookRequest, request: Request):
    """Receive an event from n8n or other external service.

    Processes the message through the agent and returns the response.
    """
    await _verify_secret(request)

    if not _lane_queue:
        raise HTTPException(status_code=503, detail="Agent not ready")

    if not _default_user_id:
        raise HTTPException(status_code=503, detail="No default user configured")

    prefix = f"[{body.source}] " if body.source else ""
    meta_note = ""
    if body.metadata:
        meta_parts = [f"{k}: {v}" for k, v in body.metadata.items()]
        meta_note = f"\n(Metadata: {', '.join(meta_parts)})"

    full_message = f"{prefix}{body.message}{meta_note}"

    logger.info("Webhook from %s: %s", body.source, body.message[:100])

    try:
        result = await _lane_queue.enqueue(_default_user_id, full_message)
        return WebhookResponse(status="ok", response=result)
    except Exception as exc:
        logger.error("Webhook processing failed: %s", exc)
        return WebhookResponse(status="error", response=str(exc))


@router.post("/notify")
async def notify_admin(body: WebhookRequest, request: Request):
    """Send a notification to the admin via Telegram (no agent processing).

    Use this for simple alerts: "Payment received from Giorgi".
    """
    await _verify_secret(request)

    if not _default_user_id:
        raise HTTPException(status_code=503, detail="No default user configured")

    # Send directly to Telegram without agent processing
    try:
        from lazyclaw.channels.telegram import get_telegram_adapter
        adapter = get_telegram_adapter()
        if adapter and adapter._admin_chat_id:
            prefix = f"[{body.source}] " if body.source else ""
            text = f"{prefix}{body.message}"
            await adapter._app.bot.send_message(
                chat_id=int(adapter._admin_chat_id),
                text=text,
            )
            return WebhookResponse(status="ok", response="Notification sent")
        return WebhookResponse(status="error", response="Telegram not configured")
    except Exception as exc:
        logger.error("Notify failed: %s", exc)
        return WebhookResponse(status="error", response=str(exc))
