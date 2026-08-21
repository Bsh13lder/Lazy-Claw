"""Official-API probing."""
from __future__ import annotations

import httpx
import pytest

from mcp_apihunter.probe import probe_official_api


def _transport(routes: dict[str, tuple[int, str]]):
    """routes: path -> (status, content_type)."""
    def handler(request: httpx.Request) -> httpx.Response:
        status, ctype = routes.get(request.url.path, (404, "text/html"))
        body = "{}" if "json" in ctype else "<html></html>"
        return httpx.Response(status, headers={"content-type": ctype}, text=body)
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_finds_openapi():
    transport = _transport({"/openapi.json": (200, "application/json")})
    result = await probe_official_api("https://himap.co", transport=transport)
    d = result.to_dict()
    assert d["official_api_found"] is True
    assert any(h["kind"] == "openapi" for h in d["hits"])


@pytest.mark.asyncio
async def test_finds_wordpress():
    transport = _transport({"/wp-json/": (200, "application/json")})
    result = await probe_official_api("https://blog.example", transport=transport)
    assert any(h.kind == "wordpress" for h in result.hits)


@pytest.mark.asyncio
async def test_nothing_found_when_all_404():
    transport = _transport({})
    result = await probe_official_api("https://himap.co", transport=transport)
    d = result.to_dict()
    assert d["official_api_found"] is False
    assert "reverse-engineer" in d["guidance"]


@pytest.mark.asyncio
async def test_html_on_openapi_is_not_a_hit():
    # An SPA that serves index.html for unknown paths must not false-positive.
    transport = _transport({"/openapi.json": (200, "text/html")})
    result = await probe_official_api("https://himap.co", transport=transport)
    assert result.hits == []
