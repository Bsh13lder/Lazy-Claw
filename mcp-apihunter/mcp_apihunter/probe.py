"""Probe a panel for an OFFICIAL API before reverse-engineering it.

An official, documented API (OpenAPI/Swagger schema, a WordPress REST root, a
GraphQL endpoint) is far more stable than scraped XHR calls, so it's always
worth a look first. `probe_official_api` fetches a handful of well-known paths
using the live session and reports what responded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from mcp_apihunter.session import ResolvedSession

# (path, kind, hint) — well-known discovery endpoints, cheapest first.
_WELL_KNOWN = [
    ("/openapi.json", "openapi", "OpenAPI 3 schema — generate endpoints from paths[]"),
    ("/swagger.json", "openapi", "Swagger schema — generate endpoints from paths[]"),
    ("/api/schema/", "openapi", "DRF/spectacular schema endpoint"),
    ("/api/schema/?format=json", "openapi", "DRF/spectacular schema (json)"),
    ("/wp-json/", "wordpress", "WordPress REST root — use /wp-json/wp/v2/posts etc."),
    ("/graphql", "graphql", "GraphQL endpoint — introspect for the schema"),
    ("/api/", "rest_root", "generic REST root — inspect for a browsable API"),
]


@dataclass(frozen=True)
class ProbeHit:
    path: str
    kind: str
    status: int
    content_type: str
    hint: str


@dataclass(frozen=True)
class ProbeResult:
    base_url: str
    hits: list[ProbeHit] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "official_api_found": bool(self.hits),
            "hits": [h.__dict__ for h in self.hits],
            "guidance": (
                "Prefer these documented endpoints — record them with "
                "panel_learn_endpoint. They change far less often than "
                "scraped XHR calls."
                if self.hits
                else "No official API detected — reverse-engineer via a network "
                "capture instead (browser action='network')."
            ),
        }


def _looks_like_api(resp: httpx.Response, kind: str) -> bool:
    if resp.status_code >= 400:
        return False
    ctype = resp.headers.get("content-type", "").lower()
    if kind in ("openapi", "wordpress", "graphql"):
        return "json" in ctype
    return "json" in ctype or "html" in ctype  # a browsable REST root is fine


async def probe_official_api(
    base_url: str,
    session: ResolvedSession | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = 10.0,
) -> ProbeResult:
    """GET each well-known path; collect the ones that look like a real API."""
    cookies = session.cookies if session else {}
    hits: list[ProbeHit] = []
    async with httpx.AsyncClient(
        transport=transport, cookies=cookies, timeout=timeout, follow_redirects=False
    ) as client:
        for path, kind, hint in _WELL_KNOWN:
            url = base_url.rstrip("/") + path
            try:
                resp = await client.get(url)
            except httpx.HTTPError:
                continue
            if _looks_like_api(resp, kind):
                hits.append(ProbeHit(
                    path=path, kind=kind, status=resp.status_code,
                    content_type=resp.headers.get("content-type", ""), hint=hint,
                ))
    return ProbeResult(base_url=base_url.rstrip("/"), hits=hits)
