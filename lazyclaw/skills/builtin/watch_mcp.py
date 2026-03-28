"""Watch MCP services (WhatsApp, Email) for new messages.

Creates a heartbeat watcher that polls MCP tools on a schedule.
Zero LLM calls during polling — just MCP tool calls + diff check.
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

# Supported services with their default poll config
_SERVICE_CONFIG = {
    "whatsapp": {
        "tool_name": "whatsapp_read",
        "default_interval_min": 2,
        "default_args": {"limit": 10},
        "contact_param": "contact",
    },
    "email": {
        "tool_name": "email_list",
        "default_interval_min": 5,
        "default_args": {"limit": 10},
        "contact_param": None,
    },
    "instagram": {
        "tool_name": "instagram_get_notifications",
        "default_interval_min": 10,
        "default_args": {},
        "contact_param": None,
    },
}


class WatchMCPSkill(BaseSkill):
    """Watch an MCP service for new messages/items."""

    def __init__(self, config=None):
        self._config = config

    @property
    def name(self) -> str:
        return "watch_messages"

    @property
    def category(self) -> str:
        return "general"

    @property
    def description(self) -> str:
        return (
            "Watch WhatsApp or Email for new messages. Uses MCP tools "
            "(no browser needed). Sends Telegram notification when new "
            "messages arrive. Zero token cost per check. "
            "Use when user says 'monitor my whatsapp', 'watch for messages', "
            "'notify me when I get a whatsapp from X', 'watch my email'. "
            "Optionally auto-reply based on instructions."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": (
                        "Service to watch: 'whatsapp', 'email', or 'instagram'"
                    ),
                    "enum": ["whatsapp", "email", "instagram"],
                },
                "contact": {
                    "type": "string",
                    "description": (
                        "Optional: specific contact/chat to watch. "
                        "For WhatsApp: name or phone number. "
                        "If omitted, watches all chats."
                    ),
                },
                "check_interval_minutes": {
                    "type": "integer",
                    "description": "How often to check in minutes. Default: 2 for WhatsApp, 5 for Email.",
                },
                "duration_hours": {
                    "type": "number",
                    "description": (
                        "How long to watch in hours. Default: 4. "
                        "Use 0 for one-shot (stop after first new message)."
                    ),
                },
                "auto_reply": {
                    "type": "string",
                    "description": (
                        "Optional: instruction for auto-replying to new messages. "
                        "E.g. 'tell them I am busy and will reply later'. "
                        "If omitted, just notifies without replying."
                    ),
                },
                "instruction": {
                    "type": "string",
                    "description": (
                        "Optional: what to watch for. "
                        "E.g. 'only notify if message mentions meeting'. "
                        "If omitted, notifies on all new messages."
                    ),
                },
            },
            "required": ["service"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"

        service = params["service"].lower()
        if service not in _SERVICE_CONFIG:
            return f"Error: Unknown service '{service}'. Supported: whatsapp, email"

        svc = _SERVICE_CONFIG[service]
        contact = params.get("contact")
        interval_min = params.get("check_interval_minutes", svc["default_interval_min"])
        duration = params.get("duration_hours", 4)
        auto_reply = params.get("auto_reply")
        instruction = params.get("instruction", "")

        # Build tool args
        tool_args = dict(svc["default_args"])
        if contact and svc["contact_param"]:
            tool_args[svc["contact_param"]] = contact

        # Check MCP connection — match by server NAME, not UUID
        from lazyclaw.mcp.manager import _active_clients
        _found_client = None
        for sid, client in _active_clients.items():
            client_name = getattr(client, "name", "") or ""
            if service in client_name.lower():
                _found_client = sid
                break
        if _found_client is None:
            # Show names not UUIDs in error
            _names = [getattr(c, "name", sid) for sid, c in _active_clients.items()]
            return (
                f"Error: {service} MCP server not connected. "
                f"Active: {', '.join(_names) or 'none'}. "
                f"Connect it first with: connect_mcp_server(name='{service}')"
            )

        # Calculate expiration
        # duration <= 0 means infinite (no expiration, continuous watch)
        # duration > 0 means watch for N hours then stop
        expires_at = None
        one_shot = False
        if duration > 0:
            from datetime import datetime, timedelta, timezone
            expires_at = (
                datetime.now(timezone.utc) + timedelta(hours=duration)
            ).isoformat()

        # Build MCP watcher context
        from lazyclaw.heartbeat.mcp_watcher import build_mcp_watcher_context
        context = build_mcp_watcher_context(
            service=service,
            tool_name=svc["tool_name"],
            tool_args=tool_args,
            check_interval=interval_min * 60,
            instruction=instruction,
            expires_at=expires_at,
            one_shot=one_shot,
            auto_reply=auto_reply,
        )

        # Create job
        from lazyclaw.heartbeat.orchestrator import create_job
        watch_name = f"Watch: {service}"
        if contact:
            watch_name += f" ({contact})"

        job_id = await create_job(
            config=self._config,
            user_id=user_id,
            name=watch_name,
            instruction=instruction or f"Watch {service} for new messages",
            job_type="watcher",
            context=context,
        )

        # Format response
        if expires_at:
            duration_str = f"for {duration} hours"
        else:
            duration_str = "indefinitely"

        contact_str = f" from {contact}" if contact else ""
        reply_str = f"\nAuto-reply: {auto_reply}" if auto_reply else ""

        return (
            f"Watching {service}{contact_str}\n"
            f"Checking every {interval_min} minutes, {duration_str}.\n"
            f"Notifications via Telegram. Zero token cost per check.{reply_str}\n"
            f"ID: {job_id[:8]}..."
        )
