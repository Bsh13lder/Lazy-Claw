"""Platform configurations for survival mode job hunting.

Each platform is served either by a dedicated MCP server or, as a last
resort, by browser-based scraping.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformConfig:
    """Immutable configuration for a job platform."""

    name: str
    base_url: str
    search_path: str  # URL path with {keywords} placeholder
    login_required: bool
    mcp_supported: bool = False  # served by a dedicated MCP server (e.g. mcp-upwork)


PLATFORMS: dict[str, PlatformConfig] = {
    "upwork": PlatformConfig(
        name="Upwork",
        base_url="https://www.upwork.com",
        search_path="/nx/search/jobs/?q={keywords}&sort=recency&per_page=20",
        login_required=True,
        mcp_supported=True,  # mcp-upwork (forked from vanooo/upwork-mcp)
    ),
}

MCP_PLATFORMS: frozenset[str] = frozenset(
    name for name, p in PLATFORMS.items() if p.mcp_supported
)
# Browser-only = login-required AND NOT covered by an MCP.
BROWSER_PLATFORMS: frozenset[str] = frozenset(
    name for name, p in PLATFORMS.items()
    if not p.mcp_supported
)
