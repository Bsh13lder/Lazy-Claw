"""Per-channel standing instructions — CRUD over the MCP watcher's auto_reply.

"When something arrives on WhatsApp, do X" is stored as the ``auto_reply``
field inside the channel's MCP watcher context (``agent_jobs`` row,
encrypted at rest). The heartbeat daemon already executes that field as a
real agent turn whenever new messages arrive, and pushes the result through
the 📨 prefixed notifier — preference-aware (telegram | app | both), so the
user SEES every execution. This module only adds the missing lifecycle:

  * :func:`set_channel_instruction`   — update the channel-wide watcher, or
    create a fresh indefinite one when no watcher exists yet
  * :func:`get_channel_instruction`   — read it back (decrypted)
  * :func:`clear_channel_instruction` — drop the instruction, keep the
    watcher notifying

Watcher selection: a channel can have several watchers (e.g. one pinned to a
contact). The CHANNEL-WIDE watcher (no contact in tool_args) is the standing
instruction's home; contact watchers keep their own auto_reply untouched.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Channels that have an MCP watcher service behind them. Telegram is the
# control channel itself — standing instructions there would loop.
SUPPORTED_CHANNELS = ("whatsapp", "email", "instagram")

# Mirrors watch_mcp._SERVICE_CONFIG defaults for freshly created watchers.
_SERVICE_DEFAULTS = {
    "whatsapp": {"tool_name": "whatsapp_read", "interval_s": 120,
                 "tool_args": {"limit": 10}, "batch_s": 300},
    "email": {"tool_name": "email_list", "interval_s": 300,
              "tool_args": {"limit": 10}, "batch_s": 0},
    "instagram": {"tool_name": "instagram_get_notifications", "interval_s": 600,
                  "tool_args": {}, "batch_s": 0},
}


def _parse_ctx(job: dict) -> dict | None:
    raw = job.get("context")
    if not raw:
        return None
    try:
        ctx = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return ctx if isinstance(ctx, dict) else None


def _is_channel_wide(ctx: dict) -> bool:
    tool_args = ctx.get("tool_args")
    return not (isinstance(tool_args, dict) and tool_args.get("contact"))


async def _find_channel_watcher(
    config: Any, user_id: str, channel: str,
) -> tuple[dict, dict] | None:
    """Newest channel-wide watcher for ``channel`` → ``(job, ctx)``.

    Falls back to a contact-pinned watcher only when no channel-wide one
    exists (so get() still surfaces an instruction set via watch_messages).
    """
    from lazyclaw.heartbeat.orchestrator import list_jobs

    jobs = await list_jobs(config, user_id)
    fallback: tuple[dict, dict] | None = None
    for job in jobs:  # list_jobs orders newest-first
        if job.get("job_type") != "watcher":
            continue
        ctx = _parse_ctx(job)
        if not ctx or ctx.get("service") != channel:
            continue
        if _is_channel_wide(ctx):
            return job, ctx
        if fallback is None:
            fallback = (job, ctx)
    return fallback


def _validate_channel(channel: str) -> None:
    if channel not in SUPPORTED_CHANNELS:
        raise ValueError(
            f"unsupported channel: {channel}. "
            f"Standing instructions work on: {', '.join(SUPPORTED_CHANNELS)}"
        )


async def get_channel_instruction(
    config: Any, user_id: str, channel: str,
) -> dict | None:
    """Return ``{channel, instruction, job_id, active}`` or ``None`` if unset."""
    _validate_channel(channel)
    found = await _find_channel_watcher(config, user_id, channel)
    if not found:
        return None
    job, ctx = found
    instruction = ctx.get("auto_reply")
    if not instruction:
        return None
    return {
        "channel": channel,
        "instruction": instruction,
        "job_id": job["id"],
        "active": job.get("status") == "active",
    }


async def set_channel_instruction(
    config: Any, user_id: str, channel: str, instruction: str,
) -> dict:
    """Set the standing instruction for a channel.

    Updates the channel-wide watcher's ``auto_reply`` (immutable rebuild of
    the context dict), creating a fresh indefinite watcher when the channel
    has none. Returns ``{channel, instruction, job_id, created}``.
    """
    _validate_channel(channel)
    instruction = (instruction or "").strip()
    if not instruction:
        raise ValueError("instruction must not be empty — use clear to remove it")

    from lazyclaw.heartbeat.orchestrator import create_job, update_job

    found = await _find_channel_watcher(config, user_id, channel)
    if found and _is_channel_wide(found[1]):
        job, ctx = found
        new_ctx = {**ctx, "auto_reply": instruction}
        await update_job(
            config, user_id, job["id"], context=json.dumps(new_ctx),
        )
        logger.info(
            "channel instruction updated (channel=%s job=%s)", channel, job["id"],
        )
        return {
            "channel": channel,
            "instruction": instruction,
            "job_id": job["id"],
            "created": False,
        }

    # No channel-wide watcher → create one (indefinite, standing).
    from lazyclaw.heartbeat.mcp_watcher import build_mcp_watcher_context

    defaults = _SERVICE_DEFAULTS[channel]
    context = build_mcp_watcher_context(
        service=channel,
        tool_name=defaults["tool_name"],
        tool_args=dict(defaults["tool_args"]),
        check_interval=defaults["interval_s"],
        instruction="",
        expires_at=None,  # standing instruction → no expiry
        one_shot=False,
        auto_reply=instruction,
        batch_window=defaults["batch_s"],
    )
    job_id = await create_job(
        config=config,
        user_id=user_id,
        name=f"Watch: {channel}",
        instruction=f"Standing instruction for {channel} messages",
        job_type="watcher",
        context=context,
    )
    logger.info(
        "channel instruction watcher created (channel=%s job=%s)", channel, job_id,
    )
    return {
        "channel": channel,
        "instruction": instruction,
        "job_id": job_id,
        "created": True,
    }


async def clear_channel_instruction(
    config: Any, user_id: str, channel: str,
) -> bool:
    """Remove the standing instruction (watcher keeps notifying). True if removed."""
    _validate_channel(channel)
    found = await _find_channel_watcher(config, user_id, channel)
    if not found:
        return False
    job, ctx = found
    if not ctx.get("auto_reply"):
        return False

    from lazyclaw.heartbeat.orchestrator import update_job

    new_ctx = {k: v for k, v in ctx.items() if k != "auto_reply"}
    await update_job(config, user_id, job["id"], context=json.dumps(new_ctx))
    logger.info(
        "channel instruction cleared (channel=%s job=%s)", channel, job["id"],
    )
    return True
