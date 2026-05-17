"""Internal-only contact lookup for MCP subprocesses.

Surfaces the encrypted unified contact store (:mod:`lazyclaw.contacts.store`)
to MCP servers that need a name → handle resolution before sending. Today the
only caller is ``mcp-whatsapp/src/index.js``; ``mcp-instagram`` and
``mcp-email`` should adopt the same bridge when they grow contact-aware UX.

Auth: shared per-process token via :func:`require_internal_token` — see
:mod:`lazyclaw.gateway.internal_auth`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from lazyclaw.config import load_config
from lazyclaw.contacts.store import find_contact
from lazyclaw.gateway.internal_auth import require_internal_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/internal/contacts", tags=["internal-contacts"])


@router.get("/search")
async def search_contacts(
    user_id: str = Query(..., description="LazyClaw user_id whose contact store to search."),
    query: str = Query(..., min_length=1, description="Name / alias / phone digits."),
    limit: int = Query(5, ge=1, le=25),
    _: None = Depends(require_internal_token),
) -> dict:
    """Return top contact matches for the given user.

    Response shape is deliberately stable — mcp-whatsapp parses ``matches[]``
    and reads ``handles.phone`` / ``handles.whatsapp_jid`` directly. Keep
    backwards-compatible if you change it.
    """
    user_id = (user_id or "").strip()
    query = (query or "").strip()
    if not user_id or not query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id and query are required",
        )

    config = load_config()
    try:
        records = await find_contact(config, user_id, query, limit=limit)
    except Exception as exc:  # pragma: no cover — defensive: don't 500 the MCP loop
        logger.warning("internal/contacts/search failed: %s", exc)
        return {"matches": [], "error": str(exc)}

    return {"matches": [r.to_public_dict() for r in records]}
