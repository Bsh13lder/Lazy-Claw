"""Graph-navigation skills — backlinks, neighbors."""
from __future__ import annotations

from lazyclaw.lazybrain import graph
from lazyclaw.skills.base import BaseSkill


class FindLinkedSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_find_linked"

    @property
    def display_name(self) -> str:
        return "Find linked notes"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "List every note that contains a [[wikilink]] to the given page name. "
            "Works even if no note with that exact title exists yet (orphaned link)."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"page_name": {"type": "string"}},
            "required": ["page_name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        notes = await graph.find_linked(
            self._config, user_id, params["page_name"]
        )
        if not notes:
            return f"(no notes link to [[{params['page_name']}]])"
        lines = [f"{len(notes)} note(s) link to [[{params['page_name']}]]:"]
        for n in notes:
            lines.append(
                f"• {n['title'] or '(untitled)'} [{n['id'][:8]}]"
            )
        return "\n".join(lines)


class GraphNeighborsSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazybrain_graph_neighbors"

    @property
    def display_name(self) -> str:
        return "Graph neighbors"

    @property
    def category(self) -> str:
        return "lazybrain"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "BFS outward from a note through wikilinks up to N hops. "
            "Returns a summary of connected notes — useful for 'what's near this idea?'"
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "note_id": {"type": "string"},
                "depth": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 3,
                    "default": 1,
                },
            },
            "required": ["note_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        depth = int(params.get("depth") or 1)
        result = await graph.get_neighbors(
            self._config, user_id, params["note_id"], depth=depth
        )
        nodes = result.get("nodes") or []
        edges = result.get("edges") or []
        if not nodes:
            return f"(no neighbors within {depth} hop(s))"
        lines = [f"Graph: {len(nodes)} node(s), {len(edges)} edge(s) within {depth} hop(s)."]
        for n in nodes:
            tag = " (root)" if n.get("is_root") else ""
            lines.append(f"• {n['label']}{tag}")
        return "\n".join(lines)
