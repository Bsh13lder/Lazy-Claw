"""PanelTools end-to-end over a tmp store with a fake session."""
from __future__ import annotations

import httpx
import pytest

from mcp_apihunter.manifest import ManifestStore
from mcp_apihunter.session import StaticSessionProvider
from mcp_apihunter.tools import PanelTools


@pytest.fixture
def tools(tmp_path):
    store = ManifestStore(tmp_path, "server-secret", "user-1")
    provider = StaticSessionProvider({"sessionid": "s", "csrftoken": "tok"})
    return PanelTools(store, provider)


@pytest.mark.asyncio
async def test_learn_requires_base_url_for_new_site(tools):
    out = await tools.learn({"site": "himap-admin", "name": "x", "method": "GET",
                             "url_template": "/api/x/"})
    assert "error" in out and "base_url" in out["error"]


@pytest.mark.asyncio
async def test_learn_list_describe_forget_flow(tools):
    saved = await tools.learn({
        "site": "himap-admin",
        "base_url": "https://himap.co",
        "name": "list_posts",
        "method": "GET",
        "url_template": "/api/posts/",
        "description": "list blog posts",
    })
    assert saved["status"] == "saved"

    assert await tools.list_sites() == {"sites": ["himap-admin"]}

    listing = await tools.list_endpoints({"site": "himap-admin"})
    assert listing["endpoints"][0]["name"] == "list_posts"
    assert listing["endpoints"][0]["mutating"] is False

    described = await tools.describe({"site": "himap-admin", "name": "list_posts"})
    assert described["url_template"] == "/api/posts/"

    forgotten = await tools.forget({"site": "himap-admin", "name": "list_posts"})
    assert forgotten["status"] == "removed_endpoint"
    assert await tools.list_endpoints({"site": "himap-admin"}) == {
        "site": "himap-admin", "base_url": "https://himap.co", "endpoints": []
    }


@pytest.mark.asyncio
async def test_call_unknown_site(tools):
    out = await tools.call({"site": "nope", "name": "x"})
    assert "error" in out


@pytest.mark.asyncio
async def test_discover_creates_shell(tools):
    out = await tools.discover({"site": "shop", "base_url": "https://shop.example"})
    assert out["status"] == "recording"
    assert out["cookie_domain"] == "shop.example"
    assert await tools.list_sites() == {"sites": ["shop"]}


@pytest.mark.asyncio
async def test_call_replays_recorded_endpoint(tmp_path, monkeypatch):
    # A full learn→call cycle, replaying through a MockTransport injected at the
    # tools→caller seam (PanelTools.call itself takes no transport).
    store = ManifestStore(tmp_path, "s", "user-1")
    tools = PanelTools(store, StaticSessionProvider({"sessionid": "s"}))
    await tools.learn({
        "site": "himap-admin", "base_url": "https://himap.co",
        "name": "get_post", "method": "GET", "url_template": "/api/posts/{id}/",
    })

    import mcp_apihunter.tools as tools_mod

    calls: list[httpx.Request] = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"id": 7, "title": "hi"},
                              headers={"content-type": "application/json"})

    real_execute = tools_mod.execute

    async def with_mock(*a, **kw):
        kw.setdefault("transport", httpx.MockTransport(handler))
        return await real_execute(*a, **kw)

    monkeypatch.setattr(tools_mod, "execute", with_mock)

    out = await tools.call({"site": "himap-admin", "name": "get_post", "args": {"id": 7}})

    assert out["status"] == "ok"
    assert out["body"] == {"id": 7, "title": "hi"}
    assert str(calls[0].url) == "https://himap.co/api/posts/7/"
