"""Search backend selector skills.

Lets the user (or the AI on their behalf) switch the search-engine backend
between auto / brave / scraper / duckduckgo. Setting lives in
``users.settings.general.search_provider`` and is honored by the
``web_search`` skill and the mcp-scraper bridge.
"""

from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


_PROVIDER_BLURBS = {
    "auto": (
        "AUTO mode — Brave Search API first (free 2k/month, clean index), "
        "then mcp-scraper Google fallback, then DuckDuckGo. Best balance."
    ),
    "brave": (
        "BRAVE mode — every search hits Brave Search API only (BRAVE_KEY). "
        "Fast, clean curated index, 2,000 free queries/month. "
        "Will fail when quota is exhausted unless you fall back to AUTO."
    ),
    "scraper": (
        "SCRAPER mode — every search goes through mcp-scraper (Google scrape "
        "via crawl4ai). Free, no key, JS-rendered, slower than Brave."
    ),
    "duckduckgo": (
        "DUCKDUCKGO mode — DDG HTML scrape (free, no key). "
        "Lower quality than Brave; rate-limits to ~5-10 req/min."
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
            "Choose which search engine backend to use for web search. "
            "Options: 'auto' (Brave → scraper → DuckDuckGo, default + "
            "recommended), 'brave' (Brave only), 'scraper' (mcp-scraper "
            "Google scrape only), 'duckduckgo' (free, lower quality). "
            "Setting is per-user, persisted, and respected by every "
            "channel (Web UI, Telegram, chat)."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "enum": ["auto", "brave", "scraper", "duckduckgo"],
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

        # Mirror non-auto picks to the legacy global default so other
        # users without a personal setting inherit a sensible choice.
        if provider in ("brave", "scraper", "duckduckgo"):
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
            "Show the current search backend (per-user) plus monthly Brave "
            "quota usage."
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
                _BRAVE_MONTHLY_LIMIT,
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
        lines.append(f"Brave:      {usage.brave_count}/{_BRAVE_MONTHLY_LIMIT}")
        lines.append(f"mcp-scraper: {usage.scraper_count} (free)")
        lines.append(f"Resets:     {usage.reset_month}")
        return "\n".join(lines)


# ─── Brave API key — set / clear via natural-language chat ──────────────
# Wraps vault_set / vault_delete with the canonical key name "brave_key"
# so the user can say "set my brave api key" / "change brave key" / etc.
# without having to remember the vault key naming convention. Vault is
# the read source — see web_search._resolve_brave_key — so a NL change
# takes effect immediately, no restart required.

class SetBraveApiKeySkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "security"

    @property
    def name(self) -> str:
        return "set_brave_api_key"

    @property
    def description(self) -> str:
        return (
            "Save (or change) the user's Brave Search API key. Stored "
            "encrypted in the vault under the key `brave_key` and read "
            "by web_search on every call — no restart needed. Use this "
            "when the user says 'set my brave api key', 'save brave "
            "key', 'change brave api key', or pastes a `BSA…`-shaped "
            "key. Get a free key at https://api-dashboard.search.brave.com "
            "(2,000 queries/month free tier)."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": (
                        "The Brave Search API subscription token "
                        "(typically starts with 'BSA')."
                    ),
                },
            },
            "required": ["key"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        params = params or {}
        key = (params.get("key") or "").strip()
        if not key:
            return (
                "Error: `key` is required — paste the Brave API token "
                "you got from https://api-dashboard.search.brave.com."
            )
        # Loose shape check — Brave keys start with BSA. Don't hard-fail
        # if the prefix is wrong; just warn so the user isn't blocked
        # by a token format they get from a future API rev.
        warning = ""
        if not key.upper().startswith("BSA"):
            warning = (
                " (note: Brave keys typically start with 'BSA' — double "
                "check this is the right token)"
            )
        from lazyclaw.crypto.vault import set_credential
        await set_credential(self._config, user_id, "brave_key", key)
        return (
            "Brave API key saved to your encrypted vault as `brave_key`. "
            "It takes effect on the next search — no restart needed."
            + warning
        )


class ClearBraveApiKeySkill(BaseSkill):
    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "security"

    @property
    def name(self) -> str:
        return "clear_brave_api_key"

    @property
    def description(self) -> str:
        return (
            "Remove the user's stored Brave Search API key from the "
            "vault. After clearing, web_search falls back to the "
            "BRAVE_KEY env var (if set) or skips Brave entirely. Use "
            "this when the user says 'remove brave key', 'forget my "
            "brave key', 'reset brave api key'."
        )

    @property
    def parameters_schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, user_id: str, params: dict) -> str:
        if not self._config:
            return "Error: Not configured"
        from lazyclaw.crypto.vault import delete_credential
        deleted = await delete_credential(self._config, user_id, "brave_key")
        if deleted:
            return (
                "Brave API key removed from your vault. Future searches "
                "will use BRAVE_KEY env var if set, otherwise fall back "
                "to mcp-scraper / DuckDuckGo."
            )
        return (
            "No Brave API key was stored in your vault. (web_search may "
            "still be using the BRAVE_KEY env var.)"
        )
