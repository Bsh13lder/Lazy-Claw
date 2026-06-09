"""Channel-agnostic façade over per-channel MCP read/send tools.

`mcp_call(tool_name, args) -> dict` is injected so the gateway is testable
without a live MCP runtime. The production caller passes the registry's MCP
invoker (see routes/inbox.py)."""
from __future__ import annotations

import json
from typing import Awaitable, Callable

from lazyclaw.comms.models import Msg, SendResult
from lazyclaw.runtime.tool_result import ToolResult

McpCall = Callable[[str, dict], Awaitable[dict]]

# channel -> (read_tool, send_tool, send_recipient_key, send_text_key, extra_send_args, extra_read_args)
#
# extra_send_args / extra_read_args are static kwargs merged into every call.
# They supply required params the thin gateway can't derive from the caller
# (e.g. Instagram's "username" = the logged-in account, email's sender "email"
# and a default "subject").  The MCP servers fill in auth details from their
# own configuration when the param names the configured account identity.
#
# Per-channel status (see gateway.py docstring):
#   whatsapp  — WORKS          (to + message / contact + limit)
#   email     — WORKS-WITH-DEFAULTS (sender email="" → server uses the single
#               configured account when omitted or empty; subject defaults to
#               "(no subject)" for v1 thin sends; search uses query=contact)
#   instagram — WORKS-WITH-DEFAULTS (username="" → server uses the configured
#               account; read maps contact → unread_only=False + no username
#               filter; send maps to_username + message)
#
# Telegram is intentionally absent: the bot can't read arbitrary contact DMs
# and is handled separately via TelegramNotifier.
_DISPATCH: dict[str, tuple[str, str, str, str, dict, dict]] = {
    "whatsapp": (
        "whatsapp_read",
        "whatsapp_send",
        "to",
        "message",
        {},                         # no extra send args
        {},                         # no extra read args (contact + limit passed dynamically)
    ),
    "email": (
        "email_search",
        "email_send",
        "to",
        "body",
        # email_send requires "email" (sender account) + "subject".
        # Passing email="" lets the server fall back to its single configured
        # account; "(no subject)" is an acceptable v1 default.
        {"email": "", "subject": "(no subject)"},
        # email_search requires "email" (account) + "query".
        # contact name is passed as "query" dynamically; email="" → server default.
        {"email": ""},
    ),
    "instagram": (
        "instagram_read_dms",
        "instagram_send_dm",
        "to_username",
        "message",
        # instagram_send_dm requires "username" (your logged-in account).
        # Passing username="" lets the server use its configured account.
        {"username": ""},
        # instagram_read_dms requires "username" (your logged-in account).
        # Passing username="" → server default; unread_only=False to see all threads.
        {"username": "", "unread_only": False},
    ),
}


# Substrings (case-insensitive) that indicate an MCP tool returned an error
# string rather than a real success payload.  Used in build_gateway._call to
# prevent wrapping error text as {"status": "sent"}.
_ERROR_MARKERS: tuple[str, ...] = (
    "not configured",
    "no session",
    "setup first",
    "failed",
    "could not",
    "error",
)


def _looks_like_error(text: str) -> bool:
    """Return True when a plain string result looks like a failure message.

    Matches any of ``_ERROR_MARKERS`` case-insensitively.  Used to stop
    error strings from being wrapped as ``{"status": "sent", "raw": text}``,
    which would cause ``ChannelGateway.send`` to report ``ok=True`` for a
    no-op send (the silent failure mode described in comms ADR).
    """
    lower = text.lower()
    return any(marker in lower for marker in _ERROR_MARKERS)


def _parse_messages(result: object) -> list[Msg]:
    """Normalize varied per-channel message shapes into a list of Msg objects."""
    if not isinstance(result, dict):
        return []
    raw = result.get("messages") or result.get("items") or result.get("emails") or []
    out: list[Msg] = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        out.append(Msg(
            sender=str(m.get("sender") or m.get("from") or m.get("author") or ""),
            text=str(m.get("content") or m.get("body") or m.get("text") or ""),
            timestamp=str(m.get("timestamp") or m.get("ts") or m.get("date") or ""),
            is_mine=bool(m.get("is_mine", False)),
        ))
    return out


class ChannelGateway:
    def __init__(self, mcp_call: McpCall):
        self._call = mcp_call

    async def read_thread(self, channel: str, contact: str, *, limit: int = 30) -> list[Msg]:
        """Live-read a thread via the per-channel read MCP tool.

        Returns an empty list for unknown channels or any MCP error — never raises.
        """
        spec = _DISPATCH.get(channel)
        if not spec:
            return []
        read_tool, _, _, _, _, extra_read = spec
        args = {**extra_read, "contact": contact, "limit": limit}
        try:
            result = await self._call(read_tool, args)
        except Exception:
            return []
        return _parse_messages(result)

    async def send(self, channel: str, contact: str, text: str) -> SendResult:
        """Dispatch a send via the per-channel MCP tool. Never raises; returns ok=False on any failure."""
        spec = _DISPATCH.get(channel)
        if not spec:
            return SendResult(ok=False, error=f"unsupported channel: {channel}")
        _, send_tool, rcpt_key, text_key, extra_send, _ = spec
        args = {**extra_send, rcpt_key: contact, text_key: text}
        try:
            result = await self._call(send_tool, args)
        except Exception as e:  # surface as typed failure, never raise
            return SendResult(ok=False, error=str(e))
        status = str(result.get("status", "")).lower() if isinstance(result, dict) else ""
        if status in ("blocked", "error", "failed"):
            return SendResult(ok=False, error=str(result))
        return SendResult(ok=True)


def build_gateway(registry: object, user_id: str) -> "ChannelGateway":
    """Factory that wires ChannelGateway.mcp_call to the real skill registry.

    MCP-bridged tools are registered under a prefixed name (``mcp_<server_id>_<tool>``),
    not their bare tool name.  Resolution order:

    1. ``registry.get_mcp_by_base_name(tool_name)`` — finds an MCP skill whose
       registered name ends with ``_<tool_name>`` (the canonical resolver for all
       6 channel tools which are MCP-bridged).
    2. ``registry.get(tool_name)``                   — exact-match fallback for any
       native (non-MCP) skill registered under its bare name.

    If both return None the call returns a clean error dict so ChannelGateway
    surfaces ``SendResult(ok=False)`` / ``[]`` rather than raising.

    BaseSkill.execute returns a JSON string or ToolResult; this adapter
    normalises the result to a dict so ChannelGateway's .get("status") /
    .get("messages") calls work correctly.
    """

    async def _call(tool_name: str, args: dict) -> dict:
        # Try MCP-prefixed resolution first (e.g. mcp_whatsapp_whatsapp_send).
        skill = None
        getter = getattr(registry, "get_mcp_by_base_name", None)
        if getter is not None:
            skill = getter(tool_name)
        # Fall back to exact-match for native (non-MCP) skills.
        if skill is None:
            skill = registry.get(tool_name)  # type: ignore[attr-defined]
        if skill is None:
            return {"status": "error", "error": f"unknown tool: {tool_name}"}
        result = await skill.execute(user_id, args)
        # Normalize to a string payload first.
        if isinstance(result, dict):
            return result
        if isinstance(result, ToolResult):  # extract the text payload
            text = result.text
        elif isinstance(result, str):
            text = result
        else:
            text = getattr(result, "text", None) or str(result)
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"status": "sent", "raw": text}
        except Exception:
            # Plain non-JSON string — detect known error patterns before
            # wrapping as a success so callers don't get a false ok=True.
            if _looks_like_error(text):
                return {"status": "error", "error": text}
            return {"status": "sent", "raw": text}

    return ChannelGateway(mcp_call=_call)
