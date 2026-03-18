"""Site memory management skills — list and delete browser site memories."""

from __future__ import annotations

import logging
from collections import defaultdict

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class ListSiteMemoriesSkill(BaseSkill):
    """List all browser site memories grouped by domain."""

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "browser"

    @property
    def name(self) -> str:
        return "list_site_memories"

    @property
    def description(self) -> str:
        return (
            "List all browser site memories — learned patterns, login flows, "
            "and navigation hints stored per domain."
        )

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.browser.site_memory import recall_all

            memories = await recall_all(self._config, user_id)
            if not memories:
                return "No site memories stored."

            grouped: dict[str, list[dict]] = defaultdict(list)
            for mem in memories:
                grouped[mem["domain"]].append(mem)

            lines = [f"Site Memories ({len(memories)} total)"]
            lines.append("=" * 40)
            for domain, entries in grouped.items():
                lines.append(f"\n{domain} ({len(entries)} memories)")
                lines.append("-" * len(domain))
                for entry in entries:
                    title_snippet = (entry["title"] or "")[:80]
                    lines.append(
                        f"  [{entry['memory_type']}] {title_snippet}"
                        f"  (success: {entry['success_count']}, "
                        f"fail: {entry['fail_count']})"
                    )
            return "\n".join(lines)
        except Exception as exc:
            return f"Error: {exc}"


class DeleteSiteMemorySkill(BaseSkill):
    """Delete all browser site memories for a specific domain."""

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "browser"

    @property
    def name(self) -> str:
        return "delete_site_memory"

    @property
    def description(self) -> str:
        return "Delete all browser site memories for a specific domain."

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Domain to clear memories for (e.g., 'github.com')",
                },
            },
            "required": ["domain"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.browser.site_memory import forget_domain

            domain = params.get("domain", "").strip()
            if not domain:
                return "Error: domain parameter is required"

            deleted = await forget_domain(self._config, user_id, domain)
            if deleted == 0:
                return f"No site memories found for '{domain}'."
            return f"Deleted {deleted} site memories for '{domain}'."
        except Exception as exc:
            return f"Error: {exc}"
