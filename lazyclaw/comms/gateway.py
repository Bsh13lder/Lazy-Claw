"""Channel-agnostic façade over per-channel MCP read/send tools.

`mcp_call(tool_name, args) -> dict` is injected so the gateway is testable
without a live MCP runtime. The production caller passes the registry's MCP
invoker (see routes/inbox.py)."""
from __future__ import annotations

import json
from typing import Awaitable, Callable

from lazyclaw.comms.models import Msg, ReadResult, SendResult
from lazyclaw.runtime.tool_result import ToolResult

McpCall = Callable[[str, dict], Awaitable[dict]]

# channel -> (read_tool, send_tool, send_recipient_key, send_text_key)
_DISPATCH = {
    "whatsapp": ("whatsapp_read", "whatsapp_send", "to", "message"),
    "email": ("email_search", "email_send", "to", "body"),
    "instagram": ("instagram_read_dms", "instagram_send_dm", "to_username", "message"),
}

# channel -> media download tool (message bytes by id). Channels without an
# entry don't support media fetch yet.
_MEDIA_DOWNLOAD = {
    "whatsapp": "whatsapp_download_media",
}


def _parse_messages(result: object) -> list[Msg]:
    """Normalize varied per-channel message shapes into a list of Msg objects."""
    if not isinstance(result, dict):
        return []
    raw = result.get("messages") or result.get("items") or result.get("emails") or []
    out: list[Msg] = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        media = m.get("media")
        msg_id = m.get("id")
        out.append(Msg(
            sender=str(m.get("sender") or m.get("from") or m.get("author") or ""),
            text=str(m.get("content") or m.get("body") or m.get("text") or ""),
            timestamp=str(m.get("timestamp") or m.get("ts") or m.get("date") or ""),
            is_mine=bool(m.get("is_mine", False)),
            id=str(msg_id) if msg_id else None,
            media=media if isinstance(media, dict) else None,
        ))
    return out


class ChannelGateway:
    def __init__(self, mcp_call: McpCall):
        self._call = mcp_call

    async def read_thread(self, channel: str, contact: str, *, limit: int = 30) -> ReadResult:
        """Live-read a thread via the per-channel read MCP tool. Never raises.

        Returns a :class:`ReadResult` so callers can tell a genuinely-empty
        thread (``ok=True, messages=()``) from a FAILED read (``ok=False``).
        Unknown channels stay ``ok=True`` + empty — that's a caller bug, not
        a dead channel.
        """
        spec = _DISPATCH.get(channel)
        if not spec:
            return ReadResult(ok=True)
        read_tool = spec[0]
        try:
            result = await self._call(read_tool, {"contact": contact, "limit": limit})
        except Exception as e:  # surface as typed failure, never raise
            return ReadResult(ok=False, error=str(e))
        # The _call adapter (and some MCP tools) report failures as an error
        # dict instead of raising — e.g. {"status": "error", "error":
        # "unknown tool: whatsapp_read"} while the MCP container restarts.
        # Treating that as an empty thread made dead channels render as
        # "No messages yet" (same defense as send()).
        if isinstance(result, dict):
            status = str(result.get("status", "")).lower()
            if status in ("blocked", "error", "failed"):
                return ReadResult(ok=False, error=str(result))
            if result.get("error"):
                return ReadResult(ok=False, error=str(result.get("error")))
        return ReadResult(ok=True, messages=tuple(_parse_messages(result)))

    async def send(self, channel: str, contact: str, text: str) -> SendResult:
        """Dispatch a send via the per-channel MCP tool. Never raises; returns ok=False on any failure."""
        spec = _DISPATCH.get(channel)
        if not spec:
            return SendResult(ok=False, error=f"unsupported channel: {channel}")
        _, send_tool, rcpt_key, text_key = spec
        try:
            result = await self._call(send_tool, {rcpt_key: contact, text_key: text})
        except Exception as e:  # surface as typed failure, never raise
            return SendResult(ok=False, error=str(e))
        if not isinstance(result, dict):
            return SendResult(ok=False, error=str(result))
        status = str(result.get("status", "")).lower()
        if status in ("blocked", "error", "failed"):
            return SendResult(ok=False, error=str(result))
        # MCP error shape without a status field — e.g. mcp-whatsapp's
        # err() returns {"error": "Contact 'X' not found."}. Treating it as
        # success made replies vanish SILENTLY (input cleared, nothing sent).
        if result.get("error"):
            return SendResult(ok=False, error=str(result.get("error")))
        return SendResult(ok=True)

    async def download_media(
        self, channel: str, message_id: str, *, contact: str | None = None,
    ) -> dict:
        """Fetch the media bytes behind a message via the per-channel download tool.

        Returns the tool's result dict (``{path, kind, mimetype, ...}`` on
        success) or ``{"error": ...}`` — never raises.
        """
        tool = _MEDIA_DOWNLOAD.get(channel)
        if not tool:
            return {"error": f"media download not supported on channel: {channel}"}
        args: dict = {"message_id": message_id}
        if contact:
            args["contact"] = contact
        try:
            result = await self._call(tool, args)
        except Exception as e:  # surface as typed failure, never raise
            return {"error": str(e)}
        return result if isinstance(result, dict) else {"error": str(result)}


def build_gateway(registry: object, user_id: str) -> "ChannelGateway":
    """Factory that wires ChannelGateway.mcp_call to the real skill registry.

    The skill registry stores BaseSkill instances retrieved by name:
        skill = registry.get(tool_name)   # returns BaseSkill | None
        result: str | ToolResult = await skill.execute(user_id, args)

    BaseSkill.execute returns a JSON string or ToolResult; this adapter
    normalises the result to a dict so ChannelGateway's .get("status") /
    .get("messages") calls work correctly.
    """

    async def _call(tool_name: str, args: dict) -> dict:
        skill = registry.get(tool_name)  # type: ignore[attr-defined]
        if skill is None:
            # MCP tools register under "mcp_<server>_<tool>" — a bare name
            # like "whatsapp_send" misses the dict lookup. Resolve by base
            # name (see feedback_test_mocks_masked_production_bug: every
            # unit test mocked the bare lookup, so this only failed live).
            getter = getattr(registry, "get_mcp_by_base_name", None)
            if callable(getter):
                skill = getter(tool_name)
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
            return {"status": "sent", "raw": text}

    return ChannelGateway(mcp_call=_call)
