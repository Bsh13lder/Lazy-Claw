"""Replay management skills — list, view, delete, and share session traces."""

from __future__ import annotations

from lazyclaw.skills.base import BaseSkill


class ListTracesSkill(BaseSkill):
    """List recent session traces."""

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "replay"

    @property
    def name(self) -> str:
        return "list_traces"

    @property
    def description(self) -> str:
        return "List recent session traces showing entry counts and time ranges."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max traces to show (default 10)",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.replay.engine import list_traces

            limit = params.get("limit", 10)
            traces = await list_traces(self._config, user_id, limit=limit)
            if not traces:
                return "No session traces found."

            lines = [f"Session traces ({len(traces)}):"]
            for t in traces:
                types = ", ".join(t["entry_types"]) if t["entry_types"] else "empty"
                lines.append(
                    f"  - {t['session_id']}: {t['entry_count']} entries "
                    f"({types}) | {t['first_ts']} to {t['last_ts']}"
                )
            return "\n".join(lines)
        except Exception as exc:
            return f"Error: {exc}"


class ViewTraceSkill(BaseSkill):
    """View the full timeline of a session trace."""

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "replay"

    @property
    def name(self) -> str:
        return "view_trace"

    @property
    def description(self) -> str:
        return "View the full timeline of a recorded session trace."

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Trace session ID to view",
                },
            },
            "required": ["session_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.replay.engine import get_trace

            entries = await get_trace(
                self._config, user_id, params["session_id"]
            )
            if not entries:
                return f"No trace found for session {params['session_id']}."

            lines = [f"Trace timeline ({len(entries)} entries):"]
            for entry in entries:
                meta = entry.get("metadata", {})
                meta_str = f" | {meta}" if meta else ""
                lines.append(
                    f"  [{entry['timestamp']}] {entry['type']}: "
                    f"{entry['content'][:100]}{meta_str}"
                )
            return "\n".join(lines)
        except Exception as exc:
            return f"Error: {exc}"


class DeleteTraceSkill(BaseSkill):
    """Delete a session trace and all its associated shares."""

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "replay"

    @property
    def name(self) -> str:
        return "delete_trace"

    @property
    def description(self) -> str:
        return "Delete a session trace and all its associated shares."

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Trace session ID to delete",
                },
            },
            "required": ["session_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.replay.engine import delete_trace

            result = await delete_trace(
                self._config, user_id, params["session_id"]
            )
            if result:
                return f"Trace {params['session_id']} and associated shares deleted."
            return f"Trace {params['session_id']} not found."
        except Exception as exc:
            return f"Error: {exc}"


class ShareTraceSkill(BaseSkill):
    """Create a shareable link for a session trace."""

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "replay"

    @property
    def name(self) -> str:
        return "share_trace"

    @property
    def description(self) -> str:
        return (
            "Create a shareable link for a session trace "
            "with optional expiration."
        )

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Trace session ID to share",
                },
                "expires_hours": {
                    "type": "integer",
                    "description": "Hours until link expires (default 24)",
                },
            },
            "required": ["session_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.replay.sharing import create_share

            expires_hours = params.get("expires_hours", 24)
            share = await create_share(
                self._config,
                user_id,
                params["session_id"],
                expires_hours=expires_hours,
            )
            return (
                f"Share link created:\n"
                f"  URL: {share['url']}\n"
                f"  Token: {share['token']}\n"
                f"  Expires: {share['expires_at']}"
            )
        except Exception as exc:
            return f"Error: {exc}"


class ManageSharesSkill(BaseSkill):
    """List or revoke trace share links."""

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "replay"

    @property
    def name(self) -> str:
        return "manage_shares"

    @property
    def description(self) -> str:
        return (
            "List active trace share links, or revoke a specific share by ID."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "revoke"],
                    "description": "Action to perform",
                },
                "share_id": {
                    "type": "string",
                    "description": "Share ID to revoke (required for revoke action)",
                },
            },
            "required": ["action"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            action = params.get("action", "list")

            if action == "list":
                from lazyclaw.replay.sharing import list_shares

                shares = await list_shares(self._config, user_id)
                if not shares:
                    return "No active share links."

                lines = [f"Active shares ({len(shares)}):"]
                for s in shares:
                    lines.append(
                        f"  - {s['share_id']}: session {s['session_id']} "
                        f"(created: {s['created_at']}, "
                        f"expires: {s['expires_at']})"
                    )
                return "\n".join(lines)

            elif action == "revoke":
                share_id = params.get("share_id", "")
                if not share_id:
                    return "Error: share_id is required for revoke action."

                from lazyclaw.replay.sharing import revoke_share

                result = await revoke_share(self._config, user_id, share_id)
                if result:
                    return f"Share {share_id} revoked."
                return f"Share {share_id} not found."

            else:
                return f"Unknown action: {action}. Use 'list' or 'revoke'."
        except Exception as exc:
            return f"Error: {exc}"
