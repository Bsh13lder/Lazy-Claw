"""Web search: Brave Search API (primary) + mcp-scraper + DuckDuckGo.

Chain order:
  Brave Search API → mcp-scraper (Google scrape) → DuckDuckGo

Brave Search API has the cleanest free tier (2,000 queries/month, no
captcha, no IP throttling) and a curated index that's far less spam-prone
than Google snippets. Scraper handles "Google has it but Brave doesn't"
edge cases. DDG is the offline-no-key fallback.

Price/flight/shopping queries skip search entirely and route to the
``browser`` skill — search snippets are cached and lie about live prices.
The skill returns a structured "open this URL in browser" instruction
and the brain dispatches the browser skill itself.

Tracks monthly Brave quota. Switchable via the /search Telegram command
or the Settings → Search tab.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

# Brave Search API free tier — 2,000 queries/month at 1 QPS. The hard
# server-side cap is on the API; we track locally for UI feedback.
_BRAVE_MONTHLY_LIMIT = 2000


@dataclass(frozen=False)
class _ProviderUsage:
    """Track monthly search counts per provider."""

    brave_count: int = 0
    scraper_count: int = 0
    reset_month: str = ""  # "2026-04" format

    def _maybe_reset(self) -> None:
        current_month = time.strftime("%Y-%m")
        if self.reset_month != current_month:
            self.brave_count = 0
            self.scraper_count = 0
            self.reset_month = current_month

    def record(self, provider: str) -> None:
        self._maybe_reset()
        if provider == "brave":
            self.brave_count += 1
        elif provider == "scraper":
            self.scraper_count += 1

    def status(self) -> str:
        self._maybe_reset()
        return (
            f"Brave: {self.brave_count}/{_BRAVE_MONTHLY_LIMIT} "
            f"| mcp-scraper: {self.scraper_count} (free)"
        )

    def brave_available(self) -> bool:
        self._maybe_reset()
        return self.brave_count < _BRAVE_MONTHLY_LIMIT


# Global singleton — survives across skill calls within one process
_usage = _ProviderUsage()

# Active provider — "auto" routes through Brave → scraper → DDG.
# Set to a single provider name to pin behavior. Per-user override
# lives in users.settings.general.search_provider.
_active_provider: str = "auto"


# In-process rate-limit cooldown — when a provider returns 429, skip it
# for the next N seconds so we don't slam the API and chain-trigger
# fallbacks on every retry.
_RATE_LIMIT_COOLDOWN_S = 60.0
_provider_cooldowns: dict[str, float] = {}


def _is_in_cooldown(provider: str) -> bool:
    until = _provider_cooldowns.get(provider, 0.0)
    return time.monotonic() < until


def _trip_cooldown(provider: str, exc: BaseException) -> bool:
    """Return True if exc indicates a rate limit; mark provider in cooldown."""
    msg = str(exc).lower()
    is_rate_limit = "429" in msg or "too many requests" in msg or "rate limit" in msg
    if is_rate_limit:
        _provider_cooldowns[provider] = time.monotonic() + _RATE_LIMIT_COOLDOWN_S
        logger.warning(
            "Search provider %s rate-limited — cooling down for %ds",
            provider, int(_RATE_LIMIT_COOLDOWN_S),
        )
    return is_rate_limit


_VALID_PROVIDERS = ("auto", "brave", "scraper", "duckduckgo")


def get_search_usage() -> _ProviderUsage:
    """Access usage tracker from Telegram commands and the /api/system/about route."""
    return _usage


def get_active_provider() -> str:
    return _active_provider


def set_active_provider(provider: str) -> str:
    """Set the process-global active provider (system default).

    Per-user preference lives in ``users.settings["general"]["search_provider"]``
    and is resolved via ``resolve_active_provider`` on every call.
    """
    global _active_provider
    if provider not in _VALID_PROVIDERS:
        return (
            f"Unknown provider: {provider}. "
            f"Use one of: {', '.join(_VALID_PROVIDERS)}."
        )
    _active_provider = provider
    return f"Search provider set to: {provider}"


async def resolve_active_provider(user_id: str | None) -> str:
    """Return the effective provider for this user.

    Reads users.settings.general.search_provider. Stale legacy values
    ("serper" / "serpapi" — removed 2026-05-02) are coerced to "auto"
    so old user rows don't break.
    """
    if not user_id:
        return _active_provider
    try:
        from lazyclaw.config import load_config
        from lazyclaw.settings.general import get_general_settings

        config = load_config()
        general = await get_general_settings(config, user_id)
        pref = str(general.get("search_provider") or "auto").lower()
    except Exception as exc:
        logger.debug("resolve_active_provider fell back to global: %s", exc)
        return _active_provider

    if pref in _VALID_PROVIDERS:
        return pref
    # Legacy / unknown — fall through to global default
    return _active_provider


# ── Brave Search API ────────────────────────────────────────────────────

async def _resolve_brave_key(user_id: str | None) -> str:
    """Find the Brave API key for this user. Vault wins over env so a
    chat-set key takes effect immediately with no restart.

    Lookup order:
      1. ``vault_get(brave_key)`` — encrypted, per-user, NL-changeable.
      2. ``BRAVE_KEY`` env var — fallback for headless/Docker setups
         where the user pre-configured via ``.env``.
    Returns empty string when neither source has it.
    """
    if user_id:
        try:
            from lazyclaw.config import load_config
            from lazyclaw.crypto.vault import get_credential
            cfg = load_config()
            stored = await get_credential(cfg, user_id, "brave_key")
            if stored:
                return stored.strip()
        except Exception as exc:
            # Vault unavailable (test ctx, missing config, locked DB) —
            # silently fall through to env. Don't fail the search.
            logger.debug("vault lookup for brave_key failed: %s", exc)
    return os.getenv("BRAVE_KEY", "").strip()


async def _brave_search(query: str, max_results: int, user_id: str | None = None) -> str:
    """Search via Brave Search API.

    Endpoint: https://api.search.brave.com/res/v1/web/search
    Auth: ``X-Subscription-Token`` header.
    Key source: per-user vault (``brave_key``) first, env (``BRAVE_KEY``) second.
    Free tier: 2,000 queries/month, 1 QPS.

    Returns formatted result string, or empty string when key is missing.
    Raises on non-2xx responses so the caller can trip the cooldown.
    """
    import httpx

    api_key = await _resolve_brave_key(user_id)
    if not api_key:
        return ""

    params = {
        "q": query,
        "count": max(1, min(max_results, 20)),  # Brave caps at 20
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params=params,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": api_key,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    _usage.record("brave")
    return _format_brave_results(data, max_results)


def _format_brave_results(data: dict, max_results: int) -> str:
    """Format Brave Search API response into readable text."""
    lines: list[str] = []

    # Brave puts answer-box-style hits in `infobox` or `discussions`
    infobox = data.get("infobox") or {}
    if isinstance(infobox, dict):
        results = infobox.get("results") or []
        if results:
            top = results[0]
            desc = top.get("description") or top.get("long_desc") or ""
            if desc:
                lines.append(f"Quick Answer: {desc.strip()}\n")

    web = data.get("web") or {}
    web_results = web.get("results") or [] if isinstance(web, dict) else []

    if not web_results:
        if lines:
            return "\n\n".join(lines)
        return "No results found."

    for i, r in enumerate(web_results[:max_results], 1):
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        desc = (r.get("description") or "").strip()
        # Brave's description has HTML <strong> tags around hit terms — strip
        desc = re.sub(r"</?strong>", "", desc)
        lines.append(f"{i}. {title}\n   {url}\n   {desc}")

    return "\n\n".join(lines)


# ── mcp-scraper bridge ──────────────────────────────────────────────────

async def _scraper_search(
    registry,
    user_id: str,
    query: str,
    max_results: int,
    search_type: str,
) -> str | None:
    """Run a Google search through the mcp-scraper pool's `search_google`.

    Returns formatted result string on success, ``None`` if the pool
    isn't registered or the call yielded no usable result. Raises on
    transport/HTTP errors so the caller can flip the cooldown.
    """
    if registry is None:
        return None

    target_skill = registry.get("mcp_scraper_search_google")
    if target_skill is None:
        # Pool wasn't registered — try on-demand spawn of shard-0.
        try:
            from lazyclaw.config import load_config
            from lazyclaw.mcp.manager import _ensure_scraper_shard
            cfg = load_config()
            spawned = await _ensure_scraper_shard(cfg, user_id, 0)
            if spawned:
                target_skill = registry.get("mcp_scraper_search_google")
        except Exception:
            logger.debug("scraper pool on-demand spawn failed", exc_info=True)
            return None

    if target_skill is None:
        return None

    raw = await target_skill.execute(
        user_id,
        {"request": {"query": query, "num_results": max_results}},
    )
    if not raw or not isinstance(raw, str):
        return None
    raw_lower = raw.lower()
    is_real_rate_limit = (
        "429" in raw
        or "quota exhausted" in raw_lower
        or "too many requests" in raw_lower
        or '"status_code": 429' in raw
        or '"rate_limited": true' in raw_lower
    )
    if is_real_rate_limit:
        raise RuntimeError(f"scraper-google rate-limited: {raw[:200]}")

    _usage.record("scraper")
    return f"[mcp-scraper | {search_type}]\n\n{raw}"


# ── DuckDuckGo offline fallback ─────────────────────────────────────────

async def _ddg_fallback(query: str, max_results: int) -> str:
    """DuckDuckGo fallback when Brave key is missing and scraper is in cooldown."""
    import asyncio

    from ddgs import DDGS

    def _search():
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))

    results = await asyncio.get_event_loop().run_in_executor(None, _search)
    if not results:
        return f"No results found for: {query}"

    formatted = []
    for i, r in enumerate(results, 1):
        formatted.append(f"{i}. {r['title']}\n   {r['href']}\n   {r['body']}")
    return "\n\n".join(formatted)


# ── Price-query routing (live data via browser, never search snippets) ──

# Common city → IATA airport code mapping for flight URL building.
_AIRPORT_CODES: dict[str, str] = {
    "barcelona": "BCN", "madrid": "MAD", "paris": "CDG", "london": "LHR",
    "berlin": "BER", "rome": "FCO", "amsterdam": "AMS", "lisbon": "LIS",
    "vienna": "VIE", "prague": "PRG", "warsaw": "WAW", "budapest": "BUD",
    "istanbul": "IST", "tbilisi": "TBS", "kutaisi": "KUT", "batumi": "BUS",
    "new york": "JFK", "los angeles": "LAX", "chicago": "ORD",
    "dubai": "DXB", "tokyo": "NRT", "bangkok": "BKK", "singapore": "SIN",
    "milan": "MXP", "munich": "MUC", "zurich": "ZRH", "athens": "ATH",
    "cairo": "CAI", "marrakech": "RAK", "antalya": "AYT", "malaga": "AGP",
    "tenerife": "TFS", "porto": "OPO", "dublin": "DUB", "stockholm": "ARN",
    "oslo": "OSL", "helsinki": "HEL", "bucharest": "OTP", "sofia": "SOF",
    "belgrade": "BEG", "tirana": "TIA", "yerevan": "EVN", "baku": "GYD",
}


def _extract_flight_params(query: str) -> dict | None:
    """Extract origin, destination, and outbound date from a flight query.

    Returns dict with departure_id, arrival_id, outbound_date or None when
    the query doesn't look like a structured flight ask.
    """
    q = query.lower()

    found_codes: list[str] = []
    explicit = re.findall(r'\b([A-Z]{3})\b', query)
    found_codes.extend(explicit)
    for city, code in _AIRPORT_CODES.items():
        if city in q and code not in found_codes:
            found_codes.append(code)

    if len(found_codes) < 2:
        return None

    # Try ISO 2026-04-07 form first — alt-ordering inside re.search is
    # per-position, not per-alternative, so the loose `(\w+)\s+(\d{1,2})`
    # form would otherwise match `on 20` from "on 2026-04-07" at an earlier
    # position and never reach the ISO alt.
    outbound_date = ""
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", query)
    if iso:
        outbound_date = f"{iso.group(1)}-{iso.group(2)}-{iso.group(3)}"
    else:
        date_match = re.search(
            r"(\w+)\s+(\d{1,2})(?:\s*,?\s*(\d{4}))?"  # April 7 2026
            r"|(\d{1,2})\s+(\w+)(?:\s+(\d{4}))?",  # 7 April 2026
            query, re.IGNORECASE,
        )
        if date_match:
            groups = date_match.groups()
            if groups[0]:  # Month Day Year
                month_name = groups[0]
                day = int(groups[1])
                year = int(groups[2]) if groups[2] else 2026
                outbound_date = _month_to_date(month_name, day, year)
            elif groups[3]:  # Day Month Year
                day = int(groups[3])
                month_name = groups[4]
                year = int(groups[5]) if groups[5] else 2026
                outbound_date = _month_to_date(month_name, day, year)

    if not outbound_date:
        return None

    return {
        "departure_id": found_codes[0],
        "arrival_id": found_codes[1],
        "outbound_date": outbound_date,
    }


def _month_to_date(month_name: str, day: int, year: int) -> str:
    """Convert month name + day + year to YYYY-MM-DD."""
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4,
        "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    m = months.get(month_name.lower(), 0)
    if not m:
        return ""
    return f"{year}-{m:02d}-{day:02d}"


# Detection patterns for queries that need LIVE data (not cached snippets).
# Search APIs (Brave, scraper, anyone) return Google's cached representation
# which is hours-stale for prices. We refuse to serve those from search.
_PRICE_PATTERN = re.compile(
    r"\b("
    r"price\s+of|how\s+much|cheapest|cheap\s+flight|cost\s+of|"
    r"in\s+stock|available|availability|book\s+(?:flight|hotel|table)|"
    r"flight\s+(?:from|to|price)|fly\s+(?:from|to)|airfare|"
    r"buy\s+now|order\s+now|deal\s+on|on\s+sale|"
    r"hotel\s+(?:in|near|price)"
    r")\b",
    re.IGNORECASE,
)


def _is_price_query(query: str, search_type: str) -> bool:
    """True when the query needs live data — search snippets cached/lie."""
    if search_type in ("flights", "shopping"):
        return True
    return bool(_PRICE_PATTERN.search(query))


def _canonical_live_url(query: str, search_type: str) -> str:
    """Build the canonical authoritative URL the browser should open
    so the agent reads a LIVE price card, not a cached snippet.

    Flights: Google Flights with extracted IATA codes + date when parseable;
             else a plain search-form URL.
    Shopping/products: Google Shopping search.
    Otherwise: regular Google search (still better via real-browser DOM
    than via cached search-API snippet).
    """
    from urllib.parse import quote_plus

    # Flights: prefer structured Google Flights URL
    if search_type == "flights" or _is_flight_query(query):
        params = _extract_flight_params(query)
        if params:
            return (
                "https://www.google.com/travel/flights?"
                f"q=Flights+from+{params['departure_id']}+to+"
                f"{params['arrival_id']}+on+{params['outbound_date']}"
            )
        return f"https://www.google.com/travel/flights?q={quote_plus(query)}"

    # Shopping / products
    if search_type == "shopping" or _is_shopping_query(query):
        return f"https://www.google.com/search?tbm=shop&q={quote_plus(query)}"

    # Default: regular Google search (real DOM, not snippet)
    return f"https://www.google.com/search?q={quote_plus(query)}"


def _is_flight_query(query: str) -> bool:
    return bool(re.search(r"\b(flight|fly|airline|airport|airfare|fare)\b", query, re.IGNORECASE))


def _is_shopping_query(query: str) -> bool:
    return bool(re.search(
        r"\b(buy|order|in\s+stock|on\s+sale|deal\s+on|cheapest|price\s+of)\b",
        query, re.IGNORECASE,
    ))


def _price_query_response(query: str, search_type: str) -> str:
    """Return the structured instruction that pushes the brain to use the
    browser instead of trusting a cached snippet."""
    url = _canonical_live_url(query, search_type)
    return (
        f"[PRICE_QUERY — live data needed, do NOT trust search snippets]\n\n"
        f"Open this URL in the browser to read the live price:\n"
        f"  {url}\n\n"
        f"Recommended sequence:\n"
        f"  1. browser(action=\"open\", url=\"{url}\")\n"
        f"  2. browser(action=\"read\")  # read the live price card\n"
        f"  3. cite the URL in your reply so the user can verify\n\n"
        f"Why: Search APIs (Brave, scraper, SerpAPI) return Google's cached "
        f"snippet which is hours-stale. Only opening the booking site in "
        f"a real browser gives the live price."
    )


# ── Search type detection (non-price classification only) ───────────────

def _detect_search_type(query: str) -> str:
    """Auto-detect search type from query keywords."""
    q = query.lower()
    flight_words = ("flight", "fly", "airline", "airport", "round trip", "one way")
    shop_words = ("buy", "purchase", "in stock", "on sale")
    news_words = ("news", "latest", "breaking", "headline", "update on")

    if any(w in q for w in flight_words):
        return "flights"
    if any(w in q for w in news_words):
        return "news"
    if any(w in q for w in shop_words) and "flight" not in q:
        return "shopping"
    return "search"


# ── Quoted-query auto-fallback (over-specification fix) ─────────────────

# Match a quoted phrase, including curly quotes some LLMs emit.
_QUOTE_RE = re.compile(r"""[“”"][^“”"]+[“”"]""")


def _query_overspecified(query: str) -> bool:
    """True when a query has 2+ quoted phrases — usually means the agent
    pinned name + address + phone all at once and search returns 0 hits."""
    return len(_QUOTE_RE.findall(query)) >= 2


def _strip_quotes(query: str) -> str:
    """Strip all quoted phrases from a query, keep the bare tokens."""
    out = _QUOTE_RE.sub(lambda m: m.group(0)[1:-1], query)
    return " ".join(out.split())


_EMPTY_RESULT_PATTERNS = (
    "no results found",
    "no search results found",
    "all search backends returned 0 results",
    "all search methods failed",
    "search backends failed",
)


def _result_is_empty(result: str | None) -> bool:
    """True when a search result text indicates 0 hits or pure failure."""
    if not result or not isinstance(result, str):
        return True
    if len(result.strip()) < 30:
        return True
    low = result.lower()
    return any(pat in low for pat in _EMPTY_RESULT_PATTERNS)


# ── Skill class ─────────────────────────────────────────────────────────

class WebSearchSkill(BaseSkill):
    def __init__(self, registry=None) -> None:
        # Registry lets us call mcp-scraper's `search_google` tool when
        # Brave returns no hits or its key is missing.
        self._registry = registry

    @property
    def read_only(self) -> bool:
        return True

    @property
    def category(self) -> str:
        return "research"

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the web — Brave Search API first (free 2k/month, "
            "clean curated index), mcp-scraper Google fallback, DuckDuckGo "
            "offline fallback. Returns titles, URLs, snippets. Supports "
            "Google query operators: `site:`, `intitle:`, `inurl:`, "
            "`\"exact phrase\"`. "
            "PRICE / FLIGHT / SHOPPING queries auto-route to a browser "
            "instruction instead of returning snippets — search APIs lie "
            "about live prices, only the real booking site is truth."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (default: 5)",
                    "default": 5,
                },
                "search_type": {
                    "type": "string",
                    "description": "Type of search: search, flights, shopping, news, maps, images",
                    "enum": ["search", "flights", "shopping", "news", "maps", "images"],
                    "default": "search",
                },
            },
            "required": ["query"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        original_query = params["query"]
        max_results = params.get("max_results", 5)
        search_type = params.get("search_type") or _detect_search_type(original_query)

        # Price/flight/shopping queries skip search entirely. Search APIs
        # return cached snippets which are hours-stale for prices. We tell
        # the brain to open the canonical booking site in the browser.
        if _is_price_query(original_query, search_type):
            logger.info(
                "Price query routed to browser instead of search: %r",
                original_query[:80],
            )
            return _price_query_response(original_query, search_type)

        # First attempt with the query as-passed.
        result = await self._search_once(
            user_id, original_query, max_results, search_type,
        )

        # Auto-fallback: when an over-specific quoted query returns 0 hits,
        # retry once with all quoted phrases stripped. The LLM sometimes
        # over-pins name + address + phone simultaneously and Brave/Google
        # returns 0; the bare equivalent returns the listing.
        if _query_overspecified(original_query) and _result_is_empty(result):
            bare = _strip_quotes(original_query)
            if bare and bare != original_query:
                logger.info(
                    "Auto-fallback: 0 hits on quoted query, retrying bare → %r",
                    bare[:80],
                )
                fallback = await self._search_once(
                    user_id, bare, max_results, search_type,
                )
                if not _result_is_empty(fallback):
                    return (
                        f"[Auto-fallback: original quoted query returned 0 hits, "
                        f"retried with bare query {bare!r}]\n\n{fallback}"
                    )
        return result

    async def _search_once(
        self,
        user_id: str,
        query: str,
        max_results: int,
        search_type: str,
    ) -> str:
        """Run one full backend cycle: Brave → scraper → DDG."""
        provider = await resolve_active_provider(user_id)
        # Vault first (chat-set), env second. See _resolve_brave_key.
        brave_key = await _resolve_brave_key(user_id)

        # User pinned a specific provider — honor it strictly.
        if provider == "duckduckgo":
            try:
                result = await _ddg_fallback(query, max_results)
                return f"[DuckDuckGo]\n\n{result}"
            except Exception as exc:
                return f"DuckDuckGo search failed: {exc}"

        if provider == "scraper":
            try:
                result = await _scraper_search(
                    self._registry, user_id, query, max_results, search_type,
                )
                if result:
                    return result
            except Exception as exc:
                _trip_cooldown("scraper", exc)
                logger.warning("mcp-scraper search failed: %s", exc)
            # Fall through to DDG
            try:
                result = await _ddg_fallback(query, max_results)
                return f"[DuckDuckGo fallback]\n\n{result}"
            except Exception as exc:
                return f"All search providers failed: {exc}"

        # ── Auto / Brave path ───────────────────────────────────────────
        # Try Brave first when a key is configured and we're not in cooldown.
        if (
            brave_key
            and _usage.brave_available()
            and not _is_in_cooldown("brave")
        ):
            try:
                result = await _brave_search(query, max_results, user_id=user_id)
                if result:
                    return f"[Brave Search | {search_type}]\n\n{result}"
            except Exception as exc:
                _trip_cooldown("brave", exc)
                logger.warning("Brave search failed: %s", exc)

        # Fall back to mcp-scraper (free, no key, JS-rendered Google)
        if not _is_in_cooldown("scraper"):
            try:
                result = await _scraper_search(
                    self._registry, user_id, query, max_results, search_type,
                )
                if result:
                    return result
            except Exception as exc:
                _trip_cooldown("scraper", exc)
                logger.warning("mcp-scraper search failed: %s", exc)

        # Final fallback: DuckDuckGo (no key required)
        try:
            result = await _ddg_fallback(query, max_results)
            return f"[DuckDuckGo fallback]\n\n{result}"
        except Exception as exc:
            in_cooldown = _is_in_cooldown("brave") or _is_in_cooldown("scraper")
            if in_cooldown:
                return (
                    "Search providers are rate-limited right now. "
                    "Retry the same query in ~60 s — do NOT escalate to "
                    "browser; the data is fine, the API just throttled."
                )
            return f"All search providers failed: {exc}"
