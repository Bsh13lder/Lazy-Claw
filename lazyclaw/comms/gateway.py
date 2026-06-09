"""Channel-agnostic façade over per-channel MCP read/send tools.

`mcp_call(tool_name, args) -> dict` is injected so the gateway is testable
without a live MCP runtime. The production caller passes the registry's MCP
invoker (see routes/inbox.py)."""
from __future__ import annotations

from typing import Awaitable, Callable

from lazyclaw.comms.models import Contact, Msg, SendResult

McpCall = Callable[[str, dict], Awaitable[dict]]

# channel -> (read_tool, send_tool, send_recipient_key, send_text_key)
_DISPATCH = {
    "whatsapp": ("whatsapp_read", "whatsapp_send", "to", "message"),
    "email": ("email_search", "email_send", "to", "body"),
    "instagram": ("instagram_read_dms", "instagram_send_dm", "to_username", "message"),
}


class ChannelGateway:
    def __init__(self, mcp_call: McpCall):
        self._call = mcp_call

    async def send(self, channel: str, contact: str, text: str) -> SendResult:
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
