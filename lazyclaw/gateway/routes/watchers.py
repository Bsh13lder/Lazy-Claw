"""Watchers REST — observability + control for slot-polling jobs.

Watcher *state* lives in agent_jobs (encrypted context JSON). The in-memory
history ring (lazyclaw.watchers.history) adds per-check stats and last-N
timeline. These endpoints decrypt and expose both, and let the UI pause /
resume / edit interval, extractor, condition / test the extractor.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException

from lazyclaw.config import load_config
from lazyclaw.gateway.auth import User, get_current_user
from lazyclaw.watchers import history as watcher_history

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/watchers", tags=["watchers"])

_config = load_config()


# ── helpers ────────────────────────────────────────────────────────────────


async def _decrypt_context(user_id: str, ctx_value) -> dict:
    """Decrypt + parse agent_jobs.context JSON blob. Returns empty on any
    error — callers should treat missing fields as nulls in the response."""
    if not ctx_value:
        return {}
    from lazyclaw.crypto.encryption import decrypt, is_encrypted
    from lazyclaw.crypto.key_manager import get_user_dek

    key = await get_user_dek(_config, user_id)
    try:
        raw = decrypt(ctx_value, key) if is_encrypted(ctx_value) else ctx_value
        return json.loads(raw) if raw else {}
    except Exception:
        logger.debug("watcher context decode failed", exc_info=True)
        return {}


def _next_check_ts(ctx: dict) -> float | None:
    """Compute when this watcher is next due based on last_check + interval."""
    interval = ctx.get("check_interval")
    last_check = ctx.get("last_check")
    if not interval:
        return None
    if not last_check:
        return datetime.now(timezone.utc).timestamp()
    try:
        last = datetime.fromisoformat(last_check)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return last.timestamp() + float(interval)
    except (ValueError, TypeError):
        return None


async def _load_template_for_watcher(user_id: str, watcher_id: str) -> dict | None:
    """Find the browser_template (if any) that owns this watcher."""
    from lazyclaw.browser import templates as tpl_store
    try:
        items = await tpl_store.list_templates(_config, user_id)
    except Exception:
        return None
    for t in items:
        if t.get("watch_job_id") == watcher_id:
            return t
    return None


async def _row_to_watcher(row, user_id: str) -> dict:
    """Turn one raw agent_jobs row into a watcher DTO the UI consumes."""
    from lazyclaw.crypto.encryption import decrypt, is_encrypted
    from lazyclaw.crypto.key_manager import get_user_dek

    key = await get_user_dek(_config, user_id)
    job_id, enc_name, job_type, enc_instruction, enc_context, status, last_run, next_run, created_at = row

    try:
        name = decrypt(enc_name, key) if enc_name and is_encrypted(enc_name) else enc_name or "unnamed"
    except Exception:
        name = "unnamed"
    try:
        instruction = (
            decrypt(enc_instruction, key)
            if enc_instruction and is_encrypted(enc_instruction)
            else enc_instruction
        )
    except Exception:
        instruction = None
    ctx = await _decrypt_context(user_id, enc_context)
    stats = watcher_history.get_stats(user_id, job_id)
    tpl = await _load_template_for_watcher(user_id, job_id)

    next_check = _next_check_ts(ctx)

    return {
        "id": job_id,
        "name": name,
        "status": status,
        "job_type": job_type,
        "instruction": instruction,
        "created_at": created_at,
        "last_run": last_run,
        "next_run": next_run,
        # From context
        "url": ctx.get("url"),
        "page_type": ctx.get("page_type"),
        "check_interval": ctx.get("check_interval"),
        "expires_at": ctx.get("expires_at"),
        "last_check": ctx.get("last_check"),
        "last_value": ctx.get("last_value"),
        "notify_template": ctx.get("notify_template"),
        "one_shot": ctx.get("one_shot", False),
        "custom_js": ctx.get("custom_js"),
        "what_to_watch": ctx.get("what_to_watch") or ctx.get("template_name"),
        "template_id": ctx.get("template_id"),
        "template_name": ctx.get("template_name") or (tpl["name"] if tpl else None),
        "template_icon": tpl.get("icon") if tpl else None,
        "template_watch_condition": tpl.get("watch_condition") if tpl else None,
        # From in-memory history
        "check_count": stats["check_count"],
        "trigger_count": stats["trigger_count"],
        "error_count": stats["error_count"],
        "last_error": stats["last_error"],
        "last_trigger_ts": stats["last_trigger_ts"],
        "last_trigger_message": stats["last_trigger_message"],
        "next_check_ts": next_check,
    }


async def _fetch_all_watchers(user_id: str) -> list[dict]:
    from lazyclaw.db.connection import db_session
    async with db_session(_config) as db:
        cursor = await db.execute(
            "SELECT id, name, job_type, instruction, context, status, "
            "last_run, next_run, created_at "
            "FROM agent_jobs "
            "WHERE user_id = ? AND job_type = 'watcher' "
            "ORDER BY created_at DESC",
            (user_id,),
        )
        rows = await cursor.fetchall()
    out: list[dict] = []
    for row in rows:
        try:
            out.append(await _row_to_watcher(row, user_id))
        except Exception:
            logger.debug("row_to_watcher failed", exc_info=True)
    return out


async def _fetch_watcher(user_id: str, watcher_id: str) -> dict | None:
    from lazyclaw.db.connection import db_session
    async with db_session(_config) as db:
        cursor = await db.execute(
            "SELECT id, name, job_type, instruction, context, status, "
            "last_run, next_run, created_at "
            "FROM agent_jobs "
            "WHERE id = ? AND user_id = ? AND job_type = 'watcher'",
            (watcher_id, user_id),
        )
        row = await cursor.fetchone()
    if not row:
        return None
    return await _row_to_watcher(row, user_id)


# ── list / summary ─────────────────────────────────────────────────────────


@router.get("")
async def list_watchers(user: User = Depends(get_current_user)):
    items = await _fetch_all_watchers(user.id)
    return {"watchers": items}


@router.get("/summary")
async def summary(user: User = Depends(get_current_user)):
    """Lightweight dashboard-tile summary (active count, last trigger)."""
    items = await _fetch_all_watchers(user.id)
    active = [w for w in items if w["status"] == "active"]
    last_trigger_ts = max(
        (w["last_trigger_ts"] for w in items if w.get("last_trigger_ts")),
        default=None,
    )
    last_trigger = next(
        (
            w for w in sorted(
                items,
                key=lambda x: x.get("last_trigger_ts") or 0,
                reverse=True,
            )
            if w.get("last_trigger_ts")
        ),
        None,
    )
    return {
        "total": len(items),
        "active": len(active),
        "paused": len(items) - len(active),
        "last_trigger_ts": last_trigger_ts,
        "last_trigger_name": last_trigger["name"] if last_trigger else None,
        "last_trigger_message": (
            last_trigger["last_trigger_message"] if last_trigger else None
        ),
    }


# ── detail + history ───────────────────────────────────────────────────────


@router.get("/{watcher_id}")
async def get_watcher(watcher_id: str, user: User = Depends(get_current_user)):
    w = await _fetch_watcher(user.id, watcher_id)
    if w is None:
        raise HTTPException(status_code=404, detail="Watcher not found")
    return w


@router.get("/{watcher_id}/history")
async def get_history(
    watcher_id: str,
    user: User = Depends(get_current_user),
):
    # Ensure the watcher belongs to this user (auth guard)
    w = await _fetch_watcher(user.id, watcher_id)
    if w is None:
        raise HTTPException(status_code=404, detail="Watcher not found")
    items = [c.to_dict() for c in watcher_history.get_history(user.id, watcher_id)]
    return {"watcher_id": watcher_id, "checks": items}


# ── control ────────────────────────────────────────────────────────────────


@router.post("/{watcher_id}/pause")
async def pause_watcher(watcher_id: str, user: User = Depends(get_current_user)):
    from lazyclaw.heartbeat.orchestrator import pause_job
    w = await _fetch_watcher(user.id, watcher_id)
    if w is None:
        raise HTTPException(status_code=404, detail="Watcher not found")
    ok = await pause_job(_config, user.id, watcher_id)
    if not ok:
        raise HTTPException(status_code=409, detail="Watcher not in active state")
    return {"status": "paused"}


@router.post("/{watcher_id}/resume")
async def resume_watcher(watcher_id: str, user: User = Depends(get_current_user)):
    """Resume a paused watcher. Cron-style resume doesn't apply (no cron
    expression), so we just flip the status back to active."""
    from lazyclaw.db.connection import db_session
    w = await _fetch_watcher(user.id, watcher_id)
    if w is None:
        raise HTTPException(status_code=404, detail="Watcher not found")
    if w["status"] == "active":
        return {"status": "active"}
    async with db_session(_config) as db:
        result = await db.execute(
            "UPDATE agent_jobs SET status = 'active' "
            "WHERE id = ? AND user_id = ? AND job_type = 'watcher'",
            (watcher_id, user.id),
        )
        await db.commit()
    if (result.rowcount or 0) == 0:
        raise HTTPException(status_code=409, detail="Could not resume watcher")
    return {"status": "active"}


@router.patch("/{watcher_id}")
async def update_watcher(
    watcher_id: str,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
):
    """Update interval / extractor / condition without recreating the job.

    Writes back the modified context JSON (encrypted) and, when
    check_interval changes, also drops last_check so the next cycle fires
    on the new cadence immediately.
    """
    from lazyclaw.crypto.encryption import decrypt, is_encrypted
    from lazyclaw.crypto.key_manager import get_user_dek
    from lazyclaw.db.connection import db_session
    from lazyclaw.heartbeat.orchestrator import update_job

    w = await _fetch_watcher(user.id, watcher_id)
    if w is None:
        raise HTTPException(status_code=404, detail="Watcher not found")

    key = await get_user_dek(_config, user.id)
    async with db_session(_config) as db:
        cursor = await db.execute(
            "SELECT context FROM agent_jobs WHERE id = ? AND user_id = ?",
            (watcher_id, user.id),
        )
        row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Watcher not found")

    try:
        raw = decrypt(row[0], key) if row[0] and is_encrypted(row[0]) else row[0] or "{}"
        ctx = json.loads(raw)
    except Exception:
        ctx = {}

    dirty = False
    if "check_interval" in payload:
        try:
            interval = int(payload["check_interval"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="check_interval must be an integer (seconds)")
        if interval < 15:
            raise HTTPException(status_code=400, detail="check_interval must be ≥ 15 seconds")
        ctx["check_interval"] = interval
        ctx["last_check"] = None  # fire immediately on new cadence
        dirty = True
    if "custom_js" in payload:
        ctx["custom_js"] = (payload["custom_js"] or "").strip() or None
        ctx["last_value"] = None  # old value no longer meaningful
        dirty = True
    if "what_to_watch" in payload:
        ctx["what_to_watch"] = (payload["what_to_watch"] or "").strip() or None
        dirty = True
    if "notify_template" in payload:
        ctx["notify_template"] = (payload["notify_template"] or "").strip() or None
        dirty = True

    if not dirty:
        raise HTTPException(status_code=422, detail="No recognized fields to update")

    await update_job(
        _config, user.id, watcher_id, context=json.dumps(ctx),
    )

    # Also mirror watch_condition/watch_extractor onto the parent template so
    # future re-runs match the edited behavior.
    if "custom_js" in payload or "what_to_watch" in payload:
        try:
            from lazyclaw.browser import templates as tpl_store
            if w.get("template_id"):
                tpl_fields: dict = {}
                if "custom_js" in payload:
                    tpl_fields["watch_extractor"] = ctx["custom_js"]
                if "what_to_watch" in payload:
                    tpl_fields["watch_condition"] = ctx["what_to_watch"]
                if tpl_fields:
                    await tpl_store.update_template(
                        _config, user.id, w["template_id"], **tpl_fields,
                    )
        except Exception:
            logger.debug("mirror to template failed", exc_info=True)

    updated = await _fetch_watcher(user.id, watcher_id)
    return updated


@router.delete("/{watcher_id}")
async def delete_watcher(watcher_id: str, user: User = Depends(get_current_user)):
    from lazyclaw.heartbeat.orchestrator import delete_job
    w = await _fetch_watcher(user.id, watcher_id)
    if w is None:
        raise HTTPException(status_code=404, detail="Watcher not found")
    ok = await delete_job(_config, user.id, watcher_id)
    watcher_history.forget_watcher(user.id, watcher_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Watcher not found")
    return {"status": "deleted"}


# ── test ───────────────────────────────────────────────────────────────────


@router.post("/{watcher_id}/test")
async def test_watcher(watcher_id: str, user: User = Depends(get_current_user)):
    """Run the watcher's extractor once against its URL, without changing
    state. Returns {extracted_value, page_type, url} so the user can verify
    the JS before leaving it running."""
    from lazyclaw.browser.browser_settings import touch_browser_activity
    from lazyclaw.browser.cdp_backend import CDPBackend

    w = await _fetch_watcher(user.id, watcher_id)
    if w is None:
        raise HTTPException(status_code=404, detail="Watcher not found")

    # Pull the raw context (decrypted) for check_watcher()
    from lazyclaw.crypto.encryption import decrypt, is_encrypted
    from lazyclaw.crypto.key_manager import get_user_dek
    from lazyclaw.db.connection import db_session

    key = await get_user_dek(_config, user.id)
    async with db_session(_config) as db:
        cursor = await db.execute(
            "SELECT context FROM agent_jobs WHERE id = ? AND user_id = ?",
            (watcher_id, user.id),
        )
        row = await cursor.fetchone()
    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="Watcher has no context")
    try:
        raw = decrypt(row[0], key) if is_encrypted(row[0]) else row[0]
        ctx = json.loads(raw)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read watcher context: {exc}")
    # Disable change-detection side-effects — clear last_value so we don't
    # accidentally "double-fire" on the very next scheduled poll. We also
    # never write the result back, so `test` is idempotent.
    probe_ctx = dict(ctx)
    probe_ctx["last_value"] = None

    touch_browser_activity()
    backend = CDPBackend(user_id=user.id)
    try:
        from lazyclaw.browser.watcher import check_watcher
        _changed, _notification, new_ctx = await check_watcher(backend, probe_ctx)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Extractor failed: {exc}")

    return {
        "url": ctx.get("url"),
        "page_type": ctx.get("page_type"),
        "extracted_value": new_ctx.get("last_value"),
        "timestamp": new_ctx.get("last_check"),
    }
