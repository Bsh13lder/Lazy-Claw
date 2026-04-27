"""Dual-provider web search: Serper.dev (primary) + SerpAPI (fallback).

Supports Google Search, Google Flights, Google Shopping, Google Maps, News.
Tracks monthly quota per provider. Switchable via /search Telegram command.
Falls back to DuckDuckGo if both API keys are missing.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

# Monthly quota limits (free tiers)
_SERPER_MONTHLY_LIMIT = 2500
_SERPAPI_MONTHLY_LIMIT = 250


@dataclass(frozen=False)
class _ProviderUsage:
    """Track monthly search counts per provider."""

    serper_count: int = 0
    serpapi_count: int = 0
    scraper_count: int = 0
    reset_month: str = ""  # "2026-04" format

    def _maybe_reset(self) -> None:
        current_month = time.strftime("%Y-%m")
        if self.reset_month != current_month:
            self.serper_count = 0
            self.serpapi_count = 0
            self.scraper_count = 0
            self.reset_month = current_month

    def record(self, provider: str) -> None:
        self._maybe_reset()
        if provider == "serper":
            self.serper_count += 1
        elif provider == "serpapi":
            self.serpapi_count += 1
        elif provider == "scraper":
            self.scraper_count += 1

    def status(self) -> str:
        self._maybe_reset()
        return (
            f"mcp-scraper: {self.scraper_count} (free) "
            f"| Serper.dev: {self.serper_count}/{_SERPER_MONTHLY_LIMIT} "
            f"| SerpAPI: {self.serpapi_count}/{_SERPAPI_MONTHLY_LIMIT}"
        )

    def serper_available(self) -> bool:
        self._maybe_reset()
        return self.serper_count < _SERPER_MONTHLY_LIMIT

    def serpapi_available(self) -> bool:
        self._maybe_reset()
        return self.serpapi_count < _SERPAPI_MONTHLY_LIMIT


# Global singleton — survives across skill calls within one process
_usage = _ProviderUsage()

# Active provider — "scraper" (default, free via mcp-scraper), or
# "serper" / "serpapi" (paid, opt-in via Telegram /search command), or
# "duckduckgo" (offline fallback). The default flipped from "serper" to
# "scraper" once mcp-scraper became the IDEAL search backbone (2026-04-26).
_active_provider: str = "scraper"

# mcp-scraper integration constants. The wrapper exposes `search_google`
# (single query), `batch_search_google` (parallel multi-query), and
# `search_and_crawl` (search + fetch). web_search uses `search_google`
# for the single-query path. Tool names are auto-prefixed by the MCP
# bridge as `mcp_<server_uuid>_<tool_name>`.
_SCRAPER_SERVER_NAME = "mcp-scraper"
_SCRAPER_SEARCH_SUFFIX = "_search_google"


def _paid_search_enabled() -> bool:
    """Whether SerpAPI / Serper.dev branches may fire.

    Soft-disabled by default (2026-04-27) — mcp-scraper covers ~all general
    search needs for free, so paid providers stay dormant. Flip
    ``LAZYCLAW_ENABLE_PAID_SEARCH=1`` to re-enable the paid chain (needed
    only for Google Flights structured pricing or as a CAPTCHA-resilient
    fallback if Google starts blocking the direct scraper).
    """
    return os.environ.get("LAZYCLAW_ENABLE_PAID_SEARCH", "").strip() in (
        "1", "true", "True", "yes", "on",
    )

# In-process rate-limit cooldown — when a provider returns 429, skip it for
# the next N seconds so we don't slam the API and chain-trigger fallback to
# browser. Without this the brain calls web_search 30× in 10 s, each one
# 429s, and the brain interprets the failure as "search broken → use
# browser instead" — which is exactly what we don't want.
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


def get_search_usage() -> _ProviderUsage:
    """Access usage tracker from Telegram commands."""
    return _usage


def get_active_provider() -> str:
    return _active_provider


def set_active_provider(provider: str) -> str:
    """Set the process-global active provider (legacy/system default).

    NOTE: per-user preference lives in ``users.settings["general"]["search_provider"]``
    and is resolved via ``resolve_active_provider`` on every call. This global
    is only the system default for users who haven't picked one.
    """
    global _active_provider
    if provider not in ("scraper", "serper", "serpapi", "duckduckgo"):
        return (
            f"Unknown provider: {provider}. "
            "Use 'scraper' (default, free), 'serper', 'serpapi', or 'duckduckgo'."
        )
    _active_provider = provider
    return f"Search provider set to: {provider}"


async def resolve_active_provider(user_id: str | None) -> str:
    """Return the effective provider for this user: per-user pref → global default.

    Values: ``"serper"``, ``"serpapi"``, ``"duckduckgo"``, or ``"auto"`` (which
    means "use the global default, then fall back"). Anything else is treated
    as ``"auto"``.
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

    if pref == "auto":
        return _active_provider
    if pref in ("serper", "serpapi", "duckduckgo"):
        return pref
    return _active_provider


async def _serper_search(query: str, max_results: int, search_type: str = "search") -> str:
    """Search via Serper.dev API."""
    import httpx

    api_key = os.getenv("SERPER_KEY", "")
    if not api_key:
        return ""

    url_map = {
        "search": "https://google.serper.dev/search",
        "flights": "https://google.serper.dev/flights",
        "shopping": "https://google.serper.dev/shopping",
        "news": "https://google.serper.dev/news",
        "maps": "https://google.serper.dev/maps",
        "images": "https://google.serper.dev/images",
    }
    url = url_map.get(search_type, url_map["search"])

    payload = {"q": query, "num": max_results}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            url,
            json=payload,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    _usage.record("serper")
    return _format_serper_results(data, search_type)


def _format_serper_results(data: dict, search_type: str) -> str:
    """Format Serper.dev response into readable text."""
    lines: list[str] = []

    if search_type == "flights":
        # Google Flights structured data
        flights = data.get("flights", [])
        if not flights:
            return "No flights found."
        for i, f in enumerate(flights, 1):
            price = f.get("price", "N/A")
            airline = f.get("airline", "Unknown")
            departure = f.get("departure_time", "")
            arrival = f.get("arrival_time", "")
            duration = f.get("duration", "")
            stops = f.get("stops", "")
            lines.append(
                f"{i}. {airline} — {price}\n"
                f"   {departure} → {arrival} ({duration})"
                f"{f' | {stops}' if stops else ' | Direct'}"
            )
        return "\n\n".join(lines)

    if search_type == "shopping":
        items = data.get("shopping", [])
        for i, item in enumerate(items, 1):
            title = item.get("title", "")
            price = item.get("price", "N/A")
            source = item.get("source", "")
            link = item.get("link", "")
            lines.append(f"{i}. {title} — {price}\n   {source}\n   {link}")
        return "\n\n".join(lines) if lines else "No shopping results."

    if search_type == "news":
        articles = data.get("news", [])
        for i, a in enumerate(articles, 1):
            lines.append(f"{i}. {a.get('title', '')}\n   {a.get('link', '')}\n   {a.get('snippet', '')}")
        return "\n\n".join(lines) if lines else "No news found."

    # Default: organic search results
    organic = data.get("organic", [])
    if not organic:
        # Check for answer box / knowledge graph
        answer = data.get("answerBox", {})
        if answer:
            return f"Answer: {answer.get('answer', answer.get('snippet', json.dumps(answer)))}"
        return "No results found."

    for i, r in enumerate(organic, 1):
        title = r.get("title", "")
        link = r.get("link", "")
        snippet = r.get("snippet", "")
        lines.append(f"{i}. {title}\n   {link}\n   {snippet}")

    # Include answer box if present
    answer = data.get("answerBox", {})
    if answer:
        answer_text = answer.get("answer", answer.get("snippet", ""))
        if answer_text:
            lines.insert(0, f"Quick Answer: {answer_text}\n")

    return "\n\n".join(lines)



# Common city → IATA airport code mapping for flight searches
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
    """Try to extract origin, destination, and date from a flight query.

    Returns dict with departure_id, arrival_id, outbound_date or None.
    """
    import re

    q = query.lower()

    # Find airport codes — either explicit (BCN) or by city name
    found_codes: list[str] = []

    # Check explicit 3-letter codes in the query
    explicit = re.findall(r'\b([A-Z]{3})\b', query)
    found_codes.extend(explicit)

    # Check city names
    for city, code in _AIRPORT_CODES.items():
        if city in q and code not in found_codes:
            found_codes.append(code)

    if len(found_codes) < 2:
        return None

    # Extract date — look for patterns like "April 7 2026", "7 April", "2026-04-07"
    date_match = re.search(
        r'(\d{4})-(\d{2})-(\d{2})'  # 2026-04-07
        r'|(\w+)\s+(\d{1,2})(?:\s*,?\s*(\d{4}))?'  # April 7 2026
        r'|(\d{1,2})\s+(\w+)(?:\s+(\d{4}))?',  # 7 April 2026
        query, re.IGNORECASE,
    )

    outbound_date = ""
    if date_match:
        groups = date_match.groups()
        if groups[0]:  # ISO format
            outbound_date = f"{groups[0]}-{groups[1]}-{groups[2]}"
        elif groups[3]:  # Month Day Year
            month_name = groups[3]
            day = int(groups[4])
            year = int(groups[5]) if groups[5] else 2026
            outbound_date = _month_to_date(month_name, day, year)
        elif groups[6]:  # Day Month Year
            day = int(groups[6])
            month_name = groups[7]
            year = int(groups[8]) if groups[8] else 2026
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


async def _serpapi_search(query: str, max_results: int, search_type: str = "search") -> str:
    """Search via SerpAPI."""
    import httpx

    api_key = os.getenv("SERPAPI_KEY", "")
    if not api_key:
        return ""

    params: dict = {
        "api_key": api_key,
        "num": max_results,
    }

    if search_type == "flights":
        flight_params = _extract_flight_params(query)
        if flight_params:
            params["engine"] = "google_flights"
            params["departure_id"] = flight_params["departure_id"]
            params["arrival_id"] = flight_params["arrival_id"]
            params["outbound_date"] = flight_params["outbound_date"]
            params["type"] = "2"  # One-way
            params["currency"] = "EUR"
            params["hl"] = "en"
            # Don't send 'q' for flights — structured params only
        else:
            # Can't parse flight params — fall back to regular Google search
            params["engine"] = "google"
            params["q"] = query
            search_type = "search"
    elif search_type == "shopping":
        params["engine"] = "google_shopping"
        params["q"] = query
    elif search_type == "news":
        params["engine"] = "google"
        params["q"] = query
        params["tbm"] = "nws"
    else:
        params["engine"] = "google"
        params["q"] = query

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            "https://serpapi.com/search",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

    _usage.record("serpapi")
    return _format_serpapi_results(data, search_type, max_results)


def _format_serpapi_results(data: dict, search_type: str, max_results: int) -> str:
    """Format SerpAPI response into readable text.

    `max_results` caps the number of shopping/organic rows we render — it
    must be passed in explicitly because Python won't see the caller's
    local. Forgetting it triggered a `NameError: max_results` for every
    SerpAPI search and silently degraded the agent to DuckDuckGo.
    """
    lines: list[str] = []

    if search_type == "flights":
        # Extract search params for booking links
        params = data.get("search_parameters", {})
        dep_id = params.get("departure_id", "")
        arr_id = params.get("arrival_id", "")
        out_date = params.get("outbound_date", "")

        for direction in ("best_flights", "other_flights"):
            flights = data.get(direction, [])
            for flight_group in flights:
                price = flight_group.get("price", "N/A")
                for leg in flight_group.get("flights", []):
                    airline = leg.get("airline", "Unknown")
                    flight_num = leg.get("flight_number", "")
                    dep = leg.get("departure_airport", {})
                    arr = leg.get("arrival_airport", {})
                    duration = leg.get("duration", "")
                    airplane = leg.get("airplane", "")
                    lines.append(
                        f"- {airline} {flight_num} — EUR {price}\n"
                        f"  {dep.get('name', '')} {dep.get('time', '')} → "
                        f"{arr.get('name', '')} {arr.get('time', '')} "
                        f"({duration} min) | {airplane}"
                    )

        if not lines:
            return "No flights found."

        # Add booking links (HTML format for Telegram embedded links)
        links: list[str] = []

        # Official airline booking URLs with date parameters
        airline_urls = {
            "Wizz Air": f"https://wizzair.com/en-gb/booking/select-flight/{dep_id}/{arr_id}/{out_date}/null/1/0/0/null",
            "Ryanair": f"https://www.ryanair.com/gb/en/trip/flights/select?adults=1&dateOut={out_date}&origin={dep_id}&destination={arr_id}",
            "easyJet": f"https://www.easyjet.com/en/booking/select?origin={dep_id}&destination={arr_id}&outbound={out_date}",
            "Vueling": f"https://www.vueling.com/en/booking/select?origin={dep_id}&destination={arr_id}&outbound={out_date}",
        }
        # Find which airlines appear in results
        seen_airlines: set[str] = set()
        for direction in ("best_flights", "other_flights"):
            for fg in data.get(direction, []):
                for leg in fg.get("flights", []):
                    seen_airlines.add(leg.get("airline", ""))

        for airline_name in seen_airlines:
            url = airline_urls.get(airline_name)
            if url:
                links.append(f'  <a href="{url}">Book on {airline_name}</a>')

        # Google Flights link (most reliable)
        google_url = data.get("search_metadata", {}).get("google_flights_url", "")
        if not google_url:
            google_url = f"https://www.google.com/travel/flights?q=flights+from+{dep_id}+to+{arr_id}+on+{out_date}"
        links.append(f'  <a href="{google_url}">Search on Google Flights</a>')

        lines.append("\nBook here:")
        lines.extend(links)

        return "\n\n".join(lines)

    if search_type == "shopping":
        for i, item in enumerate(data.get("shopping_results", [])[:max_results], 1):
            lines.append(
                f"{i}. {item.get('title', '')} — {item.get('price', 'N/A')}\n"
                f"   {item.get('source', '')} | {item.get('link', '')}"
            )
        return "\n\n".join(lines) if lines else "No shopping results."

    # Default: organic
    answer = data.get("answer_box", {})
    if answer:
        lines.append(f"Quick Answer: {answer.get('answer', answer.get('snippet', ''))}\n")

    for i, r in enumerate(data.get("organic_results", [])[:max_results], 1):
        lines.append(
            f"{i}. {r.get('title', '')}\n   {r.get('link', '')}\n   {r.get('snippet', '')}"
        )

    return "\n\n".join(lines) if lines else "No results found."


async def _scraper_search(
    registry,
    user_id: str,
    query: str,
    max_results: int,
    search_type: str,
) -> str | None:
    """Run a Google search through mcp-scraper's `search_google` tool.

    Returns formatted result string on success, ``None`` if the scraper
    server isn't registered or the call yielded no usable result. Raises
    on transport/HTTP errors so the caller can flip the cooldown.

    The scraper's tool name is dynamic (`mcp_<server_uuid>_search_google`),
    so we suffix-match on registered MCP tools and tighten with a description
    check (the bridge embeds the server name in the description).
    """
    if registry is None:
        return None

    # Suffix-match on already-registered MCP tools first.
    target_skill = None
    for tool_info in registry.list_mcp_tools():
        func = tool_info.get("function", {})
        tname = func.get("name", "")
        tdesc = func.get("description", "").lower()
        if tname.endswith(_SCRAPER_SEARCH_SUFFIX) and _SCRAPER_SERVER_NAME in tdesc:
            target_skill = registry.get(tname)
            if target_skill is not None:
                break

    # On-demand connect if mcp-scraper isn't active yet. Mirrors the
    # pattern in lazyclaw/runtime/agent.py (~line 1480) so behavior is
    # consistent between brain-side keyword injection and search-side.
    if target_skill is None:
        try:
            from lazyclaw.config import load_config
            from lazyclaw.mcp.bridge import cache_tool_schemas, register_mcp_tools
            from lazyclaw.mcp.manager import (
                connect_server, get_server_id_by_name,
            )

            cfg = load_config()
            sid = await get_server_id_by_name(cfg, user_id, _SCRAPER_SERVER_NAME)
            if not sid:
                return None
            client = await asyncio.wait_for(
                connect_server(cfg, user_id, sid), timeout=20,
            )
            tools_list = await client.list_tools()
            await cache_tool_schemas(cfg, _SCRAPER_SERVER_NAME, tools_list)
            await register_mcp_tools(
                client, registry, config=cfg, user_id=user_id,
            )
            for tool_info in registry.list_mcp_tools():
                func = tool_info.get("function", {})
                tname = func.get("name", "")
                tdesc = func.get("description", "").lower()
                if (
                    tname.endswith(_SCRAPER_SEARCH_SUFFIX)
                    and _SCRAPER_SERVER_NAME in tdesc
                ):
                    target_skill = registry.get(tname)
                    if target_skill is not None:
                        break
        except Exception:
            logger.debug("mcp-scraper on-demand connect failed", exc_info=True)
            return None

    if target_skill is None:
        return None

    # mcp-scraper's search_google takes a nested `request` dict (the
    # wrapper uses pydantic Annotated, exposed to MCP as a single object).
    # Map web_search params (query, max_results) into its expected shape.
    raw = await target_skill.execute(
        user_id,
        {"request": {"query": query, "num_results": max_results}},
    )
    if not raw or not isinstance(raw, str):
        return None
    if "rate limit" in raw.lower() or "429" in raw:
        # Surface so the caller can trip the cooldown and fall through.
        raise RuntimeError(f"scraper-google rate-limited: {raw[:200]}")

    _usage.record("scraper")
    return f"[mcp-scraper | {search_type}]\n\n{raw}"


async def _ddg_fallback(query: str, max_results: int) -> str:
    """DuckDuckGo fallback when no API keys are configured."""
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


def _detect_search_type(query: str) -> str:
    """Auto-detect search type from query keywords."""
    q = query.lower()
    flight_words = ("flight", "fly", "airline", "airport", "bcn", "kut", "round trip", "one way")
    shop_words = ("buy", "price of", "cheapest", "shop", "purchase", "cost of")
    news_words = ("news", "latest", "breaking", "headline", "update on")

    if any(w in q for w in flight_words):
        return "flights"
    if any(w in q for w in news_words):
        return "news"
    # Shopping detection is intentionally narrow to avoid false positives
    if any(w in q for w in shop_words) and "flight" not in q:
        return "shopping"
    return "search"


class WebSearchSkill(BaseSkill):
    def __init__(self, registry=None) -> None:
        # Registry is needed so we can call mcp-scraper's `search_google`
        # tool (the new primary provider). Optional — falls back to the
        # SerpAPI/Serper/DDG chain if registry is not wired in.
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
            "Search the web — runs through mcp-scraper (free, no API key, "
            "JS-rendered Google). DuckDuckGo is the offline fallback if scraper "
            "is in cooldown. Paid providers (SerpAPI/Serper.dev) are off by "
            "default; flip LAZYCLAW_ENABLE_PAID_SEARCH=1 to enable them — needed "
            "only for Google Flights structured pricing. "
            "Returns titles, URLs, snippets. Supports Google query operators in "
            "the `query` field: `site:domain.com`, `intitle:word`, `inurl:word`, "
            "`\"exact phrase\"`. The result URL itself often contains the answer "
            "(e.g. an instagram.com/<handle>/ URL gives you the handle directly)."
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
        query = params["query"]
        max_results = params.get("max_results", 5)
        search_type = params.get("search_type") or _detect_search_type(query)

        provider = await resolve_active_provider(user_id)
        serper_key = os.getenv("SERPER_KEY", "")
        serpapi_key = os.getenv("SERPAPI_KEY", "")

        # ── Primary path: mcp-scraper's search_google (free, JS-rendered) ──
        # Tried first for ALL non-flight searches unless the user explicitly
        # opted into a paid provider via the Telegram /search command.
        # Flights still need SerpAPI (only provider with the Google Flights
        # engine), so we skip scraper for that branch.
        _scraper_eligible = (
            search_type != "flights"
            and provider not in {"serpapi", "serper", "duckduckgo"}
            and not _is_in_cooldown("scraper")
        )
        if _scraper_eligible:
            try:
                result = await _scraper_search(
                    self._registry, user_id, query, max_results, search_type,
                )
                if result:
                    return result
            except Exception as exc:
                _trip_cooldown("scraper", exc)
                logger.warning("mcp-scraper search failed: %s", exc)

        # If the user explicitly picked DuckDuckGo, honor it immediately
        # (skip the paid providers entirely — except flights, which DDG can't do).
        if provider == "duckduckgo" and search_type != "flights":
            try:
                result = await _ddg_fallback(query, max_results)
                return f"[DuckDuckGo]\n\n{result}"
            except Exception as exc:
                return f"DuckDuckGo search failed: {exc}"

        # ── Paid-provider chain (SerpAPI / Serper.dev) ────────────────────
        # Soft-disabled by default since 2026-04-27 — mcp-scraper handles
        # general search for free. Flip LAZYCLAW_ENABLE_PAID_SEARCH=1 to
        # re-enable (needed only for Google Flights structured pricing,
        # or as a CAPTCHA-resilient fallback if Google blocks the direct
        # scraper). When disabled, control falls through to DuckDuckGo.
        _paid_enabled = _paid_search_enabled()

        # Flights: ONLY SerpAPI has Google Flights structured pricing.
        # Even when paid is disabled, log so the user knows why their
        # flight query came back empty.
        if search_type == "flights" and not _paid_enabled:
            logger.info(
                "Flight query received but LAZYCLAW_ENABLE_PAID_SEARCH is off "
                "— flights need SerpAPI; returning DuckDuckGo text fallback"
            )

        if (
            _paid_enabled and search_type == "flights"
            and serpapi_key and _usage.serpapi_available()
        ):
            try:
                result = await _serpapi_search(query, max_results, "flights")
                if result:
                    return f"[SerpAPI | Google Flights]\n\n{result}"
            except Exception as exc:
                logger.warning("SerpAPI flights failed: %s", exc)
            # Flight fallback: regular search via Serper
            if serper_key and _usage.serper_available():
                try:
                    result = await _serper_search(query, max_results, "search")
                    if result:
                        return f"[Serper.dev | web search fallback for flights]\n\n{result}"
                except Exception as exc:
                    logger.warning("Serper fallback for flights failed: %s", exc)

        # Non-flights: respect active provider, fall back to the other.
        # Each provider gets its own try/except so a 429 on the primary
        # cleanly falls through to the secondary instead of jumping to
        # DuckDuckGo (the previous shared try/except did the latter).
        if _paid_enabled and search_type != "flights":
            primary_rate_limited = False

            # Primary provider attempt
            if (
                provider == "serper" and serper_key
                and _usage.serper_available() and not _is_in_cooldown("serper")
            ):
                try:
                    result = await _serper_search(query, max_results, search_type)
                    if result:
                        return f"[Serper.dev | {search_type}]\n\n{result}"
                except Exception as exc:
                    primary_rate_limited = _trip_cooldown("serper", exc)
                    logger.warning("Serper.dev failed: %s", exc)
            elif (
                provider == "serpapi" and serpapi_key
                and _usage.serpapi_available() and not _is_in_cooldown("serpapi")
            ):
                try:
                    result = await _serpapi_search(query, max_results, search_type)
                    if result:
                        return f"[SerpAPI | {search_type}]\n\n{result}"
                except Exception as exc:
                    primary_rate_limited = _trip_cooldown("serpapi", exc)
                    logger.warning("SerpAPI failed: %s", exc)

            # Secondary provider attempt — separate try so a primary 429
            # doesn't skip this branch.
            if (
                provider == "serper" and serpapi_key
                and _usage.serpapi_available() and not _is_in_cooldown("serpapi")
            ):
                try:
                    result = await _serpapi_search(query, max_results, search_type)
                    if result:
                        return f"[SerpAPI fallback | {search_type}]\n\n{result}"
                except Exception as exc:
                    _trip_cooldown("serpapi", exc)
                    logger.warning("SerpAPI fallback failed: %s", exc)
            elif (
                provider == "serpapi" and serper_key
                and _usage.serper_available() and not _is_in_cooldown("serper")
            ):
                try:
                    result = await _serper_search(query, max_results, search_type)
                    if result:
                        return f"[Serper.dev fallback | {search_type}]\n\n{result}"
                except Exception as exc:
                    _trip_cooldown("serper", exc)
                    logger.warning("Serper.dev fallback failed: %s", exc)

            # If both paid providers were rate-limited, surface that
            # specifically so the brain knows it's a transient API issue —
            # NOT a "search is broken, try browser" signal. This matters
            # because the brain's training bias is to escalate to browser
            # whenever a tool returns "failed" wording.
            if primary_rate_limited:
                logger.info(
                    "Search providers rate-limited — falling back to DuckDuckGo "
                    "(provider in %ds cooldown)", int(_RATE_LIMIT_COOLDOWN_S),
                )

        # Final fallback: DuckDuckGo (no flights/shopping support)
        try:
            result = await _ddg_fallback(query, max_results)
            return f"[DuckDuckGo fallback]\n\n{result}"
        except Exception as exc:
            # Phrase carefully: don't say "all search providers failed" if it
            # was rate-limit cooldown — that phrasing pushes the brain to
            # browser. Instead tell it to retry the query in a moment.
            in_cooldown = _is_in_cooldown("serper") or _is_in_cooldown("serpapi")
            if in_cooldown:
                return (
                    "Search providers are rate-limited right now. "
                    "Retry the same query in ~60 s — do NOT escalate to "
                    "browser; the data is fine, the API just throttled."
                )
            return f"All search providers failed: {exc}"
