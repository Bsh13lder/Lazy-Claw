"""Keyword research — localized SERP expansion for multiple seeds.

Additive skill: stitches existing Serper.dev / SerpAPI plumbing with
Google localization (gl/hl) to extract:
  - relatedSearches (long-tail bottom-of-SERP)
  - peopleAlsoAsk (question-shaped keywords for blog posts)
  - organic result titles (real headlines ranking for the seed)
  - autocomplete-style suggestions (via Serper 'autocomplete' endpoint
    when available)

Read-only, no DB writes, no side effects. Runs n queries per call (one
per seed) against whichever SERP provider the user already has a key
for. Falls back gracefully when no provider is available.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)

_MAX_SEEDS = 25
_DEFAULT_RESULTS = 10


async def _serper_keyword_fetch(
    seed: str,
    gl: str,
    hl: str,
    num: int,
) -> dict[str, list[str]]:
    """Fetch one seed's SERP data from Serper.dev and extract keyword-rich fields."""
    import httpx

    api_key = os.getenv("SERPER_KEY", "")
    if not api_key:
        return {}

    payload = {"q": seed, "gl": gl, "hl": hl, "num": num}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://google.serper.dev/search",
                json=payload,
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.debug("Serper keyword fetch failed for seed=%r: %s", seed, exc)
        return {}

    related = [
        (r.get("query") or "").strip()
        for r in (data.get("relatedSearches") or [])
        if isinstance(r, dict)
    ]
    paa = [
        (q.get("question") or "").strip()
        for q in (data.get("peopleAlsoAsk") or [])
        if isinstance(q, dict)
    ]
    organic_titles = [
        (o.get("title") or "").strip()
        for o in (data.get("organic") or [])[:num]
        if isinstance(o, dict)
    ]
    return {
        "related": [x for x in related if x],
        "paa": [x for x in paa if x],
        "titles": [x for x in organic_titles if x],
    }


async def _serper_autocomplete(seed: str, gl: str, hl: str) -> list[str]:
    """Fetch Google autocomplete suggestions via Serper's dedicated endpoint."""
    import httpx

    api_key = os.getenv("SERPER_KEY", "")
    if not api_key:
        return []

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://google.serper.dev/autocomplete",
                json={"q": seed, "gl": gl, "hl": hl},
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.debug("Serper autocomplete failed for seed=%r: %s", seed, exc)
        return []

    suggestions = data.get("suggestions") or []
    out: list[str] = []
    for s in suggestions:
        if isinstance(s, dict):
            val = (s.get("value") or s.get("query") or "").strip()
            if val:
                out.append(val)
        elif isinstance(s, str) and s.strip():
            out.append(s.strip())
    return out


def _dedupe(items: list[str]) -> list[str]:
    """Order-preserving case-insensitive dedupe, trims whitespace."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        s = " ".join((raw or "").split())
        key = s.lower()
        if s and key not in seen:
            seen.add(key)
            out.append(s)
    return out


class KeywordResearchSkill(BaseSkill):
    """Expand a list of seed keywords into long-tail variants, questions, and
    competitor headlines for a target language/country market.

    Returns grouped sections (related_searches, people_also_ask, autocomplete,
    competitor_titles) plus a merged deduplicated blob suitable for pasting
    into a Google Sheet. No volume/difficulty numbers — those require a paid
    SEO API (see DataForSEO / Ahrefs MCP recommendations). This skill covers
    the free part: topic discovery and real user queries.
    """

    def __init__(self, config=None):
        self._config = config

    @property
    def category(self) -> str:
        return "research"

    @property
    def name(self) -> str:
        return "keyword_research"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "Expand seed keywords into long-tail variants, question-form "
            "keywords (People Also Ask), autocomplete suggestions, and "
            "competitor headlines — localized to a target country + "
            "language (Google gl/hl). Use when the user wants blog topic "
            "ideation, content gap research, or SEO keyword expansion "
            "without paying for an SEO tool. Does NOT return search "
            "volume or difficulty — for that, use DataForSEO."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "seeds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of seed keywords or phrases to expand. Up "
                        f"to {_MAX_SEEDS}. Each seed fires 1 SERP query + "
                        "1 autocomplete query."
                    ),
                },
                "country": {
                    "type": "string",
                    "description": (
                        "Google geo code (gl). e.g. 'es' Spain, 'us' USA, "
                        "'mx' Mexico, 'uk' UK. Default: 'es'."
                    ),
                },
                "language": {
                    "type": "string",
                    "description": (
                        "Google language code (hl). e.g. 'es' Spanish, "
                        "'en' English, 'pt' Portuguese. Default: 'es'."
                    ),
                },
                "num_results": {
                    "type": "integer",
                    "description": (
                        f"Organic results per seed (1-20). Default "
                        f"{_DEFAULT_RESULTS}. Higher = more competitor "
                        "headlines but uses more API quota."
                    ),
                },
            },
            "required": ["seeds"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        seeds_raw = params.get("seeds") or []
        if not isinstance(seeds_raw, list) or not seeds_raw:
            return "Error: seeds must be a non-empty array of strings."

        seeds = _dedupe([str(s) for s in seeds_raw])[:_MAX_SEEDS]
        country = (params.get("country") or "es").strip().lower() or "es"
        language = (params.get("language") or "es").strip().lower() or "es"
        num = int(params.get("num_results") or _DEFAULT_RESULTS)
        num = max(1, min(num, 20))

        has_serper = bool(os.getenv("SERPER_KEY"))
        if not has_serper:
            return (
                "No SERPER_KEY configured — keyword_research needs a "
                "Serper.dev key. Get one free at https://serper.dev and "
                "add to your env, OR ask the user to set SERPER_KEY."
            )

        related_all: list[str] = []
        paa_all: list[str] = []
        titles_all: list[str] = []
        autocomplete_all: list[str] = []
        per_seed: list[str] = []

        for seed in seeds:
            serp = await _serper_keyword_fetch(seed, country, language, num)
            ac = await _serper_autocomplete(seed, country, language)

            related_all.extend(serp.get("related", []))
            paa_all.extend(serp.get("paa", []))
            titles_all.extend(serp.get("titles", []))
            autocomplete_all.extend(ac)

            per_seed.append(
                f"  • {seed}: "
                f"{len(serp.get('related', []))} related, "
                f"{len(serp.get('paa', []))} PAA, "
                f"{len(ac)} autocomplete, "
                f"{len(serp.get('titles', []))} titles"
            )

        related = _dedupe(related_all)
        paa = _dedupe(paa_all)
        autocomplete = _dedupe(autocomplete_all)
        titles = _dedupe(titles_all)

        all_keywords = _dedupe(related + paa + autocomplete)

        lines = [
            f"# Keyword research — {len(seeds)} seed(s), {country.upper()}/{language}",
            "",
            "## Per-seed counts",
            *per_seed,
            "",
            f"## People Also Ask ({len(paa)}) — question-form keywords, best for blog post titles",
            *(f"  - {q}" for q in paa[:60]),
            "",
            f"## Related searches ({len(related)}) — long-tail variants Google itself shows",
            *(f"  - {r}" for r in related[:60]),
            "",
            f"## Autocomplete ({len(autocomplete)}) — what people start typing",
            *(f"  - {a}" for a in autocomplete[:60]),
            "",
            f"## Competitor headlines ({len(titles)}) — what already ranks",
            *(f"  - {t}" for t in titles[:40]),
            "",
            f"## Merged deduplicated list ({len(all_keywords)}) — paste into Google Sheet column A",
            *(f"  {k}" for k in all_keywords),
            "",
            "Note: no search volumes returned — free SERP data only. For "
            "volume + difficulty, add a DataForSEO MCP.",
        ]
        return "\n".join(lines)
