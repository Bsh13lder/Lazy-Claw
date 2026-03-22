"""Platform configurations for survival mode job hunting.

Each platform declares whether it's supported by JobSpy MCP (fast, API-based)
or requires browser-based scraping (slower, needs login).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformConfig:
    """Immutable configuration for a job platform."""

    name: str
    base_url: str
    search_path: str  # URL path with {keywords} placeholder
    jobspy_supported: bool
    login_required: bool


PLATFORMS: dict[str, PlatformConfig] = {
    "upwork": PlatformConfig(
        name="Upwork",
        base_url="https://www.upwork.com",
        search_path="/nx/search/jobs/?q={keywords}&sort=recency&per_page=20",
        jobspy_supported=False,
        login_required=True,
    ),
    "indeed": PlatformConfig(
        name="Indeed",
        base_url="https://www.indeed.com",
        search_path="/jobs?q={keywords}&fromage=3",
        jobspy_supported=True,
        login_required=False,
    ),
    "glassdoor": PlatformConfig(
        name="Glassdoor",
        base_url="https://www.glassdoor.com",
        search_path="",
        jobspy_supported=True,
        login_required=False,
    ),
}

JOBSPY_PLATFORMS: frozenset[str] = frozenset(
    name for name, p in PLATFORMS.items() if p.jobspy_supported
)
BROWSER_PLATFORMS: frozenset[str] = frozenset(
    name for name, p in PLATFORMS.items() if not p.jobspy_supported
)
