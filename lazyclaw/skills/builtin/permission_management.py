"""Permission management skills.

Five skills for viewing/updating permission settings, managing pending
approval requests, and querying the security audit log.
"""

from __future__ import annotations

from lazyclaw.skills.base import BaseSkill

# Known categories for distinguishing category vs skill targets
_KNOWN_CATEGORIES = frozenset({
    "general",
    "utility",
    "search",
    "research",
    "memory",
    "vault",
    "browser",
    "computer",
    "skills",
    "custom",
    "security",
    "ai_management",
    "mcp",
    "system",
    "permissions",
})


# ---------------------------------------------------------------------------
# 1. ShowPermissionsSkill
# ---------------------------------------------------------------------------


class ShowPermissionsSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "permissions"

    @property
    def name(self) -> str:
        return "show_permissions"

    @property
    def description(self) -> str:
        return (
            "Show current permission levels for all skill categories "
            "and any individual skill overrides."
        )

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.permissions.settings import get_permission_settings

            settings = await get_permission_settings(self._config, user_id)

            lines = ["== Category Defaults =="]
            category_defaults = settings.get("category_defaults", {})
            for cat, level in sorted(category_defaults.items()):
                lines.append(f"  {cat}: {level}")

            overrides = settings.get("skill_overrides", {})
            lines.append("")
            lines.append("== Skill Overrides ==")
            if overrides:
                for skill, level in sorted(overrides.items()):
                    lines.append(f"  {skill}: {level}")
            else:
                lines.append("  (none)")

            timeout = settings.get("auto_approve_timeout", 300)
            heartbeat = settings.get("require_approval_for_heartbeat", True)
            lines.append("")
            lines.append("== Settings ==")
            lines.append(f"  Auto-approve timeout: {timeout}s")
            lines.append(
                f"  Require approval for heartbeat: {'yes' if heartbeat else 'no'}"
            )

            return "\n".join(lines)
        except Exception as exc:
            return f"Error: {exc}"


# ---------------------------------------------------------------------------
# 2. SetPermissionSkill
# ---------------------------------------------------------------------------


class SetPermissionSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "permissions"

    @property
    def name(self) -> str:
        return "set_permission"

    @property
    def description(self) -> str:
        return (
            "Set the permission level (allow, ask, or deny) for a "
            "skill category or specific skill."
        )

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Category name or skill name",
                },
                "level": {
                    "type": "string",
                    "enum": ["allow", "ask", "deny"],
                    "description": "Permission level to set",
                },
            },
            "required": ["target", "level"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.permissions.settings import update_permission_settings

            target = params["target"]
            level = params["level"]

            if target in _KNOWN_CATEGORIES:
                updates = {"category_defaults": {target: level}}
                kind = "category"
            else:
                updates = {"skill_overrides": {target: level}}
                kind = "skill"

            await update_permission_settings(self._config, user_id, updates)
            return f"Permission for {kind} '{target}' set to '{level}'."
        except ValueError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error: {exc}"


# ---------------------------------------------------------------------------
# 3. ListPendingApprovalsSkill
# ---------------------------------------------------------------------------


class ListPendingApprovalsSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "permissions"

    @property
    def name(self) -> str:
        return "list_pending_approvals"

    @property
    def description(self) -> str:
        return (
            "List pending tool approval requests that are waiting "
            "for your decision."
        )

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.permissions.approvals import get_pending

            pending = await get_pending(self._config, user_id)

            if not pending:
                return "No pending approval requests."

            lines = [f"== Pending Approvals ({len(pending)}) =="]
            for req in pending:
                lines.append(
                    f"  ID: {req.id}\n"
                    f"    Skill: {req.skill_name}\n"
                    f"    Source: {req.source}\n"
                    f"    Created: {req.created_at}\n"
                    f"    Expires: {req.expires_at}"
                )

            return "\n".join(lines)
        except Exception as exc:
            return f"Error: {exc}"


# ---------------------------------------------------------------------------
# 4. DecideApprovalSkill
# ---------------------------------------------------------------------------


class DecideApprovalSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "permissions"

    @property
    def name(self) -> str:
        return "decide_approval"

    @property
    def description(self) -> str:
        return "Approve or deny a pending tool approval request."

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "approval_id": {
                    "type": "string",
                    "description": "ID of the approval request",
                },
                "decision": {
                    "type": "string",
                    "enum": ["approve", "deny"],
                    "description": "Whether to approve or deny the request",
                },
            },
            "required": ["approval_id", "decision"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.permissions.approvals import approve_request, deny_request

            approval_id = params["approval_id"]
            decision = params["decision"]

            if decision == "approve":
                result = await approve_request(
                    self._config, approval_id, decided_by=user_id
                )
            else:
                result = await deny_request(
                    self._config, approval_id, decided_by=user_id
                )

            if result is None:
                return (
                    f"Approval request '{approval_id}' not found or "
                    "already resolved."
                )

            return (
                f"Request {result.id} for '{result.skill_name}' "
                f"has been {result.status}."
            )
        except Exception as exc:
            return f"Error: {exc}"


# ---------------------------------------------------------------------------
# 5. QueryAuditLogSkill
# ---------------------------------------------------------------------------


class QueryAuditLogSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "permissions"

    @property
    def name(self) -> str:
        return "query_audit_log"

    @property
    def description(self) -> str:
        return (
            "Query the security audit log to see recent tool usage, "
            "approvals, and denials."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Filter by action type (optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max entries (default 20)",
                },
            },
            "required": [],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.permissions.audit import query_log

            action_filter = params.get("action")
            limit = params.get("limit", 20)

            entries = await query_log(
                self._config,
                user_id,
                action_filter=action_filter,
                limit=limit,
            )

            if not entries:
                return "No audit log entries found."

            lines = [f"== Audit Log ({len(entries)} entries) =="]
            for entry in entries:
                skill_part = f" [{entry.skill_name}]" if entry.skill_name else ""
                lines.append(
                    f"  [{entry.created_at}] {entry.action}{skill_part}"
                    f" (source: {entry.source})"
                )

            return "\n".join(lines)
        except Exception as exc:
            return f"Error: {exc}"
