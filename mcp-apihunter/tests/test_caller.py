"""Request building, CSRF injection, replay, and outcome classification."""
from __future__ import annotations

import json

import httpx
import pytest

from mcp_apihunter.caller import MissingArguments, execute
from mcp_apihunter.manifest import CsrfSpec, Endpoint, Manifest
from mcp_apihunter.session import SessionUnavailable, StaticSessionProvider


def _manifest(*endpoints):
    m = Manifest(
        site="himap-admin",
        base_url="https://himap.co",
        cookie_domain="himap.co",
        login_url="https://himap.co/admin/login/",
    )
    for ep in endpoints:
        m = m.with_endpoint(ep)
    return m


def _recorder():
    """A MockTransport that records requests and returns a scripted response."""
    calls: list[httpx.Request] = []
    box = {"status": 200, "json": {"ok": True}, "headers": {}}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            box["status"],
            json=box["json"],
            headers={"content-type": "application/json", **box["headers"]},
        )

    return httpx.MockTransport(handler), calls, box


@pytest.mark.asyncio
async def test_get_renders_path_and_returns_body():
    ep = Endpoint(name="get_post", method="GET", url_template="/api/posts/{id}/")
    transport, calls, _ = _recorder()
    provider = StaticSessionProvider({"sessionid": "abc"})

    outcome = await execute(
        _manifest(ep), ep, {"id": 42}, provider, transport=transport
    )

    assert outcome.status == "ok"
    assert outcome.body == {"ok": True}
    assert str(calls[0].url) == "https://himap.co/api/posts/42/"
    assert "sessionid=abc" in calls[0].headers.get("cookie", "")


@pytest.mark.asyncio
async def test_post_json_body_and_csrf_header():
    ep = Endpoint(
        name="post_blog",
        method="POST",
        url_template="/api/posts/",
        body_type="json",
        body_template={"title": "{title}", "published": "{published}"},
        csrf=CsrfSpec(source="cookie", cookie_name="csrftoken", target="header",
                      target_name="X-CSRFToken"),
        mutating=True,
    )
    transport, calls, _ = _recorder()
    provider = StaticSessionProvider({"csrftoken": "tok123", "sessionid": "s"})

    outcome = await execute(
        _manifest(ep), ep,
        {"title": "Hello", "published": True},
        provider, confirm=True, transport=transport,
    )

    assert outcome.status == "ok"
    sent = calls[0]
    assert sent.headers.get("X-CSRFToken") == "tok123"
    body = json.loads(sent.content)
    # Full-placeholder value keeps its native type (bool, not "True").
    assert body == {"title": "Hello", "published": True}


@pytest.mark.asyncio
async def test_mutating_without_confirm_is_gated():
    ep = Endpoint(name="del_post", method="DELETE", url_template="/api/posts/{id}/",
                  mutating=True)
    transport, calls, _ = _recorder()
    provider = StaticSessionProvider({"sessionid": "s"})

    outcome = await execute(_manifest(ep), ep, {"id": 1}, provider, transport=transport)

    assert outcome.status == "needs_confirm"
    assert calls == []  # nothing was sent


@pytest.mark.asyncio
async def test_403_maps_to_needs_relogin():
    ep = Endpoint(name="get_post", method="GET", url_template="/api/posts/")
    transport, _, box = _recorder()
    box["status"] = 403
    provider = StaticSessionProvider({"sessionid": "stale"})

    outcome = await execute(_manifest(ep), ep, {}, provider, transport=transport)
    assert outcome.status == "needs_relogin"
    assert outcome.http_status == 403


@pytest.mark.asyncio
async def test_login_redirect_maps_to_needs_relogin():
    ep = Endpoint(name="get_post", method="GET", url_template="/api/posts/")

    def handler(request):
        return httpx.Response(302, headers={"location": "https://himap.co/admin/login/"})

    provider = StaticSessionProvider({"sessionid": "stale"})
    outcome = await execute(
        _manifest(ep), ep, {}, provider, transport=httpx.MockTransport(handler)
    )
    assert outcome.status == "needs_relogin"


@pytest.mark.asyncio
async def test_session_unavailable_maps_to_needs_relogin():
    ep = Endpoint(name="get_post", method="GET", url_template="/api/posts/")

    class DeadProvider:
        async def resolve(self, cookie_domain):
            raise SessionUnavailable("browser is not logged in")

    outcome = await execute(_manifest(ep), ep, {}, DeadProvider())
    assert outcome.status == "needs_relogin"
    assert "login" in outcome.message.lower()


@pytest.mark.asyncio
async def test_missing_argument_raises():
    ep = Endpoint(name="get_post", method="GET", url_template="/api/posts/{id}/")
    provider = StaticSessionProvider({"sessionid": "s"})
    with pytest.raises(MissingArguments) as exc:
        await execute(_manifest(ep), ep, {}, provider)
    assert "id" in exc.value.names


@pytest.mark.asyncio
async def test_unexpected_status_is_error():
    ep = Endpoint(name="get_post", method="GET", url_template="/api/posts/",
                  success_status=[200])
    transport, _, box = _recorder()
    box["status"] = 500
    box["json"] = {"detail": "boom"}
    provider = StaticSessionProvider({"sessionid": "s"})

    outcome = await execute(_manifest(ep), ep, {}, provider, transport=transport)
    assert outcome.status == "error"
    assert outcome.http_status == 500
    assert outcome.body == {"detail": "boom"}
