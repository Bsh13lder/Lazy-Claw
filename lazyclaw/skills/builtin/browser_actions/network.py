"""Browser `network` action — query the XHR/fetch ring buffer.

Gives the agent a structured view of what the page actually loaded, which
is often more useful than the DOM on SPAs. Response bodies are fetched
lazily (only when ``include_body=true`` and only for the records that pass
the filter) to avoid CDP backpressure.
"""

from __future__ import annotations

import json
import logging

from lazyclaw.browser import network_inspector
from lazyclaw.browser.action_errors import (
    RETRY_RE_READ,
    ActionError,
    ActionErrorCode,
)

from .backends import get_backend

logger = logging.getLogger(__name__)

# Hard cap on how many bytes of response-body previews we return per query.
# Keeps the LLM context bounded even when include_body=true on a big list.
_MAX_TOTAL_BODY_BYTES = 200 * 1024
_BODY_PREVIEW_BYTES = 2048


async def action_network(
    user_id: str, params: dict, tab_context, config, snapshot_mgr,
) -> str:
    """Query recent network traffic captured by the CDP inspector."""
    backend = await get_backend(user_id, tab_context)

    url_substring = (params.get("url_substring") or params.get("url") or "").strip() or None
    method = (params.get("method") or "").strip() or None
    status_min = params.get("status_min")
    status_max = params.get("status_max")
    since_ts = params.get("since_ts")
    only_failed = bool(params.get("only_failed", False))
    limit = int(params.get("limit") or 20)
    include_body = bool(params.get("include_body", False))

    # Clamp to sane range — agents sometimes pass very large limits.
    limit = max(1, min(limit, 50))

    records, truncated, total_seen = network_inspector.query(
        user_id,
        url_substring=url_substring,
        method=method,
        status_min=status_min,
        status_max=status_max,
        since_ts=since_ts,
        only_failed=only_failed,
        limit=limit,
    )

    if not records and total_seen == 0:
        return str(ActionError(
            code=ActionErrorCode.NOT_FOUND,
            message="No captured network requests match the filter.",
            hint=(
                "Try loosening the filter, wait a moment for the page to "
                "finish loading, or drop url_substring to see all requests."
            ),
            retry_strategy=RETRY_RE_READ,
        ))

    result_rows: list[dict] = []
    body_budget = _MAX_TOTAL_BODY_BYTES

    for rec in records:
        row: dict = {
            "url": rec.url,
            "method": rec.method,
            "status": rec.status,
            "mime_type": rec.mime_type,
            "size": rec.response_size,
            "from_cache": rec.from_cache,
            "failed": rec.failed,
            "request_ts": rec.request_ts,
        }
        if rec.error_text:
            row["error"] = rec.error_text

        if include_body and _is_textual_mime(rec.mime_type) and body_budget > 0:
            preview = await _fetch_body_preview(backend, rec.request_id, body_budget)
            if preview is not None:
                row["body_preview"] = preview
                body_budget -= len(preview)
        result_rows.append(row)

    payload = {
        "records": result_rows,
        "returned": len(result_rows),
        "total_seen": total_seen,
        "ring_truncated": truncated,
    }
    if truncated:
        payload["note"] = (
            "Ring buffer is full (oldest entries evicted). Increase page "
            "activity or filter tighter to see a smaller slice."
        )
    return json.dumps(payload, indent=2)


def _is_textual_mime(mime_type: str | None) -> bool:
    if not mime_type:
        return False
    m = mime_type.lower()
    if "json" in m or "javascript" in m or "xml" in m:
        return True
    if m.startswith("text/"):
        return True
    return False


async def _fetch_body_preview(backend, request_id: str, budget: int) -> str | None:
    """Lazy fetch via Network.getResponseBody, capped at _BODY_PREVIEW_BYTES.

    Returns None on race conditions (stream closed, request cancelled,
    response pruned by the browser) — never raises, never kills the caller.
    """
    if not request_id:
        return None
    conn = getattr(backend, "_conn", None)
    if not conn or not getattr(conn, "is_connected", False):
        return None
    try:
        resp = await conn.send(
            "Network.getResponseBody", {"requestId": request_id},
        )
    except Exception:
        # Normal race: body already gone. Skip this one; don't blow up the batch.
        logger.debug("Network.getResponseBody failed for %s", request_id, exc_info=True)
        return None

    body = resp.get("body", "") or ""
    if resp.get("base64Encoded"):
        # Non-text body despite the mime check; skip binary.
        return None
    take = min(_BODY_PREVIEW_BYTES, budget)
    return body[:take]
