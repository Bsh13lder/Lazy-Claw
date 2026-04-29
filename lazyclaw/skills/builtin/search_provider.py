"""Search backend selector skills.

Lets the user (or the AI on their behalf) switch the search-engine backend
between auto / serper / serpapi / duckduckgo. Setting lives in
``users.settings.general.search_provider`` and is honored by both the
``web_search`` skill and the mcp-scraper bridge (auto-injected as
``backend=`` on scraper search calls).
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


_PROVIDER_BLURBS = {
    "auto": (
        "AUTO mode — Serper first, falls back to SerpAPI then googlesearch-python. "
        "Best balance: cheapest reliable result, with redundancy."
    ),
    "serper": (
        "SERPER mode — every search hits Serper.dev only. "
        "Fastest (~1-2s), cheapest (~$0.30/1k), 2,500 free credits. "
        "Will fail if Serper quota is exhausted."
    ),
    "serpapi": (
        "SERPAPI mode — every search hits SerpAPI only. "
        "More features than Serper, ~3x cost. "
        "Will fail if SerpAPI quota is exhausted."
    ),
    "duckduckgo": (
        "DUCKDUCKGO mode — DDG HTML scrape (free, no key). "
        "Lower quality than Serper/SerpAPI; rate-limits to ~5-10 req/min. "
        "Only honored by the web_search skill; mcp-scraper falls back to auto."
    ),
}


class SetSearchProviderSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "ai_management"

    @property
    def name(self) -> str:
        return "set_search_provider"

    @property
    def description(self) -> str:
        return (
            "Choose which search engine backend to use for web search and "
            "scraper search. Options: 'auto' (Serper → SerpAPI fallback, "
            "default + recommended), 'serper' (Serper only), 'serpapi' "
            "(SerpAPI only), 'duckduckgo' (free, lower quality). Setting "
            "is per-user, persisted, and respected by every channel "
            "(Web UI, Telegram, chat)."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "enum": ["auto", "serper", "serpapi", "duckduckgo"],
                    "description": (
                        "Which search backend to use. 'auto' is recommended "
                        "for most users."
                    ),
                },
            },
            "required": ["provider"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        provider = str(params.get("provider", "")).lower().strip()
        if provider not in _PROVIDER_BLURBS:
            valid = ", ".join(sorted(_PROVIDER_BLURBS.keys()))
            return f"Error: provider must be one of {valid} (got {provider!r})"
        try:
            from lazyclaw.settings.general import update_general_settings
            await update_general_settings(
                self._config, user_id, {"search_provider": provider},
            )
        except ValueError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            logger.warning("set_search_provider failed", exc_info=True)
            return f"Error setting search provider: {exc}"

        # Mirror to legacy global default for paid providers, so other
        # users without a personal setting inherit a sensible choice.
        if provider in ("serper", "serpapi"):
            try:
                from lazyclaw.skills.builtin.web_search import set_active_provider
                set_active_provider(provider)
            except Exception:
                logger.debug("set_active_provider mirror failed", exc_info=True)

        return _PROVIDER_BLURBS[provider]


class ShowSearchProviderSkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "ai_management"

    @property
    def name(self) -> str:
        return "show_search_provider"

    @property
    def description(self) -> str:
        return (
            "Show the current search backend (per-user) plus quota usage "
            "for Serper and SerpAPI."
        )

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        try:
            from lazyclaw.settings.general import get_general_settings
            from lazyclaw.skills.builtin.web_search import (
                _SERPAPI_MONTHLY_LIMIT,
                _SERPER_MONTHLY_LIMIT,
                get_search_usage,
            )
        except Exception as exc:
            return f"Error reading settings: {exc}"
        general = await get_general_settings(self._config, user_id)
        usage = get_search_usage()
        provider = str(general.get("search_provider") or "auto")
        lines = [f"Search provider: {provider.upper()}"]
        lines.append("─" * 20)
        lines.append(_PROVIDER_BLURBS.get(provider, ""))
        lines.append("")
        lines.append(
            f"Serper:  {usage.serper_count}/{_SERPER_MONTHLY_LIMIT}"
        )
        lines.append(
            f"SerpAPI: {usage.serpapi_count}/{_SERPAPI_MONTHLY_LIMIT}"
        )
        lines.append(f"Resets:  {usage.reset_month}")
        return "\n".join(lines)
