"""EXPLORE specialist's allowed_skills must include mcp-scraper tool names.

Without this, EXPLORE subagents fall back to `browser` even when the brain
told them to use scraper — because the specialist's whitelist filters them
out at materialization time. This test pins the contract: if the registry
exposes any mcp-scraper tool, the EXPLORE specialist must see it.
"""

from __future__ import annotations

from lazyclaw.runtime.dispatcher import (
    AgentDispatcher,
    AgentType,
    SubagentConfig,
    _EXPLORE_TOOLS,
)


class _FakeRegistry:
    """Stub that returns a handful of mcp-scraper tools + one unrelated MCP tool."""

    def __init__(self):
        self._uuid = "f33e5b73-743b-4e6a-8a55-2a53c750965d"

    def list_mcp_tools(self) -> list[dict]:
        return [
            {
                "function": {
                    "name": f"mcp_{self._uuid}_extract_entities",
                    "description": (
                        "Self-hosted scraper — JS-rendered crawl, entity extraction. "
                        "Part of mcp-scraper."
                    ),
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "function": {
                    "name": f"mcp_{self._uuid}_crawl_url",
                    "description": (
                        "Fetch a URL and return markdown. Part of mcp-scraper."
                    ),
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "function": {
                    "name": f"mcp_{self._uuid}_search_google",
                    "description": (
                        "Google search via mcp-scraper (no API key needed)."
                    ),
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "function": {
                    "name": "mcp_other-server_send_email",
                    "description": "Send an email via mcp-email.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]


def _build_dispatcher() -> AgentDispatcher:
    d = AgentDispatcher.__new__(AgentDispatcher)
    d._config = None
    d._eco_router = None
    d._registry = _FakeRegistry()
    d._permission_checker = None
    return d


def test_explore_specialist_includes_scraper_tools():
    d = _build_dispatcher()
    cfg = SubagentConfig(
        agent_type=AgentType.EXPLORE,
        task="find an email",
        tool_names=None,
    )
    spec = d._make_specialist(cfg)
    allowed = set(spec.allowed_skills)

    # Static base whitelist still present
    assert "web_search" in allowed
    assert "browser" in allowed
    # Dynamic mcp-scraper tools added
    assert any(n.endswith("_extract_entities") for n in allowed)
    assert any(n.endswith("_crawl_url") for n in allowed)
    assert any(n.endswith("_search_google") for n in allowed)
    # Unrelated MCP tool is NOT mistakenly included
    assert "mcp_other-server_send_email" not in allowed


def test_explore_with_explicit_tool_names_still_unions_scraper():
    """If caller pinned tool_names, scraper still gets unioned in.

    This matters for keyword-based subagent dispatch where the brain narrows
    the toolset — we don't want the narrowing to accidentally lock scraper out.
    """
    d = _build_dispatcher()
    cfg = SubagentConfig(
        agent_type=AgentType.EXPLORE,
        task="find an email",
        tool_names=("web_search",),
    )
    spec = d._make_specialist(cfg)
    allowed = set(spec.allowed_skills)
    assert "web_search" in allowed
    assert any(n.endswith("_extract_entities") for n in allowed)


def test_explore_handles_missing_registry_gracefully():
    """If list_mcp_tools blows up, we still get the static base allowed set."""
    d = AgentDispatcher.__new__(AgentDispatcher)
    d._config = None
    d._eco_router = None
    d._permission_checker = None

    class _Boom:
        def list_mcp_tools(self):
            raise RuntimeError("registry not ready")

    d._registry = _Boom()
    cfg = SubagentConfig(
        agent_type=AgentType.EXPLORE, task="x", tool_names=None,
    )
    spec = d._make_specialist(cfg)
    assert set(spec.allowed_skills) == set(_EXPLORE_TOOLS)
