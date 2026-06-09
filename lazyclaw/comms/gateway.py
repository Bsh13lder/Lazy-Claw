"""Channel-agnostic façade over per-channel MCP read/send tools.

`mcp_call(tool_name, args) -> dict` is injected so the gateway is testable
without a live MCP runtime. The production caller passes the registry's MCP
invoker (see routes/inbox.py)."""
from __future__ import annotations

from typing import Awaitable, Callable

from lazyclaw.comms.models import Msg, SendResult

McpCall = Callable[[str, dict], Awaitable[dict]]

# channel -> (read_tool, send_tool, send_recipient_key, send_text_key)
_DISPATCH = {
    "whatsapp": ("whatsapp_read", "whatsapp_send", "to", "message"),
    "email": ("email_search", "email_send", "to", "body"),
    "instagram": ("instagram_read_dms", "instagram_send_dm", "to_username", "message"),
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
        read_tool = spec[0]
        try:
            result = await self._call(read_tool, {"contact": contact, "limit": limit})
        except Exception:
            return []
        return _parse_messages(result)

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
        status = str(result.get("status", "")).lower() if isinstance(result, dict) else ""
        if status in ("blocked", "error", "failed"):
            return SendResult(ok=False, error=str(result))
        return SendResult(ok=True)


def build_gateway(registry: object, user_id: str) -> "ChannelGateway":
    """Factory that wires ChannelGateway.mcp_call to the real skill registry.

    The skill registry stores BaseSkill instances retrieved by name:
        skill = registry.get(tool_name)   # returns BaseSkill | None
        result: str = await skill.execute(user_id, args)

    BaseSkill.execute returns a JSON string; this adapter parses it to a dict
    so ChannelGateway's .get("status") / .get("messages") calls work correctly.
    """
    import json as _json

    async def _call(tool_name: str, args: dict) -> dict:
        skill = registry.get(tool_name)  # type: ignore[attr-defined]
        if skill is None:
            raise RuntimeError(f"skill not found in registry: {tool_name!r}")
        result = await skill.execute(user_id, args)
        if isinstance(result, dict):
            return result
        if isinstance(result, str):
            try:
                parsed = _json.loads(result)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
            # Non-JSON string — wrap so callers can still .get("status")
            return {"status": "sent", "raw": result}
        # Unexpected object type — extract known payload attributes or wrap
        for attr in ("result", "output", "data"):
            data = getattr(result, attr, None)
            if isinstance(data, dict):
                return data
            if isinstance(data, str):
                try:
                    parsed = _json.loads(data)
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    pass
                return {"status": "sent", "raw": data}
        return {"status": "sent", "raw": str(result)}

    return ChannelGateway(mcp_call=_call)
