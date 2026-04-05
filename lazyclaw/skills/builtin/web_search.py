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
    reset_month: str = ""  # "2026-04" format

    def _maybe_reset(self) -> None:
        current_month = time.strftime("%Y-%m")
        if self.reset_month != current_month:
            self.serper_count = 0
            self.serpapi_count = 0
            self.reset_month = current_month

    def record(self, provider: str) -> None:
        self._maybe_reset()
        if provider == "serper":
            self.serper_count += 1
        elif provider == "serpapi":
            self.serpapi_count += 1

    def status(self) -> str:
        self._maybe_reset()
        return (
            f"Serper.dev: {self.serper_count}/{_SERPER_MONTHLY_LIMIT} "
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

# Active provider: "serper" or "serpapi" — changeable via Telegram
_active_provider: str = "serper"


def get_search_usage() -> _ProviderUsage:
    """Access usage tracker from Telegram commands."""
    return _usage


def get_active_provider() -> str:
    return _active_provider


def set_active_provider(provider: str) -> str:
    """Set active provider. Returns confirmation message."""
    global _active_provider
    if provider not in ("serper", "serpapi"):
        return f"Unknown provider: {provider}. Use 'serper' or 'serpapi'."
    _active_provider = provider
    return f"Search provider set to: {provider}"


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
    return _format_serpapi_results(data, search_type)


def _format_serpapi_results(data: dict, search_type: str) -> str:
    """Format SerpAPI response into readable text."""
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
            "Search the web using Google via Serper.dev or SerpAPI. "
            "Supports Google Search, Flights, Shopping, News, Maps. "
            "Auto-detects flight queries and returns structured pricing data. "
            "Returns real-time results with titles, URLs, snippets, and prices."
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

        provider = _active_provider
        serper_key = os.getenv("SERPER_KEY", "")
        serpapi_key = os.getenv("SERPAPI_KEY", "")

        # Flights: ALWAYS use SerpAPI (only provider with Google Flights engine)
        if search_type == "flights" and serpapi_key and _usage.serpapi_available():
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

        # Non-flights: respect active provider, fall back to the other
        if search_type != "flights":
            try:
                if provider == "serper" and serper_key and _usage.serper_available():
                    result = await _serper_search(query, max_results, search_type)
                    if result:
                        return f"[Serper.dev | {search_type}]\n\n{result}"

                if serpapi_key and _usage.serpapi_available():
                    result = await _serpapi_search(query, max_results, search_type)
                    if result:
                        return f"[SerpAPI | {search_type}]\n\n{result}"

                if provider == "serpapi" and serper_key and _usage.serper_available():
                    result = await _serper_search(query, max_results, search_type)
                    if result:
                        return f"[Serper.dev fallback | {search_type}]\n\n{result}"

            except Exception as exc:
                logger.warning("Search API failed (%s): %s", provider, exc)

        # Final fallback: DuckDuckGo (no flights/shopping support)
        try:
            result = await _ddg_fallback(query, max_results)
            return f"[DuckDuckGo fallback]\n\n{result}"
        except Exception as exc:
            return f"All search providers failed: {exc}"
