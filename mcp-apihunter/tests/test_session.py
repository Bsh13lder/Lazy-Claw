"""Cookie-domain scoping, base_url host extraction, CDP host resolution."""
from __future__ import annotations

import json

import pytest

from mcp_apihunter import session as session_mod
from mcp_apihunter.session import CdpSessionProvider, _domain_matches, cookie_domain_for


@pytest.mark.parametrize(
    "cookie_domain,host,expected",
    [
        ("himap.co", "himap.co", True),
        (".himap.co", "himap.co", True),
        (".himap.co", "admin.himap.co", True),
        ("himap.co", "admin.himap.co", True),
        ("himap.co", "evilhimap.co", False),
        ("other.com", "himap.co", False),
    ],
)
def test_domain_matches(cookie_domain, host, expected):
    assert _domain_matches(cookie_domain, host) is expected


def test_cookie_domain_for():
    assert cookie_domain_for("https://himap.co/admin/") == "himap.co"
    assert cookie_domain_for("http://localhost:8000") == "localhost"


def test_cookie_domain_for_rejects_garbage():
    with pytest.raises(ValueError):
        cookie_domain_for("not-a-url")


# --- CDP host resolution -----------------------------------------------------

BROWSER_WS = "ws://127.0.0.1:9222/devtools/browser/abc"
COOKIES = [{"name": "sessionid", "value": "abc", "domain": "himap.co"}]


def _patch_http(monkeypatch, ws_url: str = BROWSER_WS) -> list[str]:
    """Answer /json/version offline; returns the list of URLs actually dialed."""
    dialed: list[str] = []

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"webSocketDebuggerUrl": ws_url}

    class _Client:
        def __init__(self, **kwargs) -> None:
            return None

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        async def get(self, url: str) -> _Response:
            dialed.append(url)
            return _Response()

    monkeypatch.setattr(session_mod.httpx, "AsyncClient", _Client)
    return dialed


def _patch_ws(monkeypatch, cookies: list[dict] = COOKIES) -> list[str]:
    """Answer Storage.getCookies offline; returns the ws URLs actually dialed."""
    dialed: list[str] = []

    class _Socket:
        async def send(self, _raw: str) -> None:
            return None

        async def recv(self) -> str:
            return json.dumps({"id": 1, "result": {"cookies": cookies}})

    class _Connect:
        def __init__(self, url: str, **kwargs) -> None:
            dialed.append(url)

        async def __aenter__(self) -> _Socket:
            return _Socket()

        async def __aexit__(self, *exc) -> bool:
            return False

    monkeypatch.setattr("websockets.asyncio.client.connect", _Connect)
    return dialed


@pytest.mark.asyncio
async def test_cdp_dials_resolved_ip(monkeypatch):
    # Chromium answers 500 to a `Host: host.docker.internal` header, so both
    # the HTTP probe and the ws handshake must carry the resolved IP.
    monkeypatch.setattr("socket.gethostbyname", lambda host: "192.168.65.254")
    http_urls = _patch_http(monkeypatch)
    ws_urls = _patch_ws(monkeypatch)

    provider = CdpSessionProvider("host.docker.internal", 9222)
    session = await provider.resolve("himap.co")

    assert http_urls == ["http://192.168.65.254:9222/json/version"]
    assert ws_urls == ["ws://192.168.65.254:9222/devtools/browser/abc"]
    assert session.cookies == {"sessionid": "abc"}


@pytest.mark.asyncio
async def test_cdp_falls_back_to_raw_host_when_dns_fails(monkeypatch):
    def _unresolvable(host: str) -> str:
        raise OSError("Name or service not known")

    monkeypatch.setattr("socket.gethostbyname", _unresolvable)
    http_urls = _patch_http(monkeypatch)
    ws_urls = _patch_ws(monkeypatch)

    provider = CdpSessionProvider("host.docker.internal", 9222)
    await provider.resolve("himap.co")

    assert http_urls == ["http://host.docker.internal:9222/json/version"]
    assert ws_urls == ["ws://host.docker.internal:9222/devtools/browser/abc"]


@pytest.mark.asyncio
async def test_cdp_resolves_dns_on_every_call(monkeypatch):
    # Lazy per-call resolution: a bridge that moves (or comes back) is picked
    # up without restarting the MCP process.
    answers = iter(["192.168.65.254", "192.168.65.99"])
    monkeypatch.setattr("socket.gethostbyname", lambda host: next(answers))
    http_urls = _patch_http(monkeypatch)
    _patch_ws(monkeypatch)

    provider = CdpSessionProvider("host.docker.internal", 9222)
    await provider.resolve("himap.co")
    await provider.resolve("himap.co")

    assert http_urls == [
        "http://192.168.65.254:9222/json/version",
        "http://192.168.65.99:9222/json/version",
    ]


@pytest.mark.asyncio
async def test_cdp_ip_host_is_dialed_unchanged(monkeypatch):
    # A literal IP needs no special-casing — resolving it yields itself.
    http_urls = _patch_http(monkeypatch)
    ws_urls = _patch_ws(monkeypatch)

    provider = CdpSessionProvider("127.0.0.1", 9222)
    await provider.resolve("himap.co")

    assert http_urls == ["http://127.0.0.1:9222/json/version"]
    assert ws_urls == ["ws://127.0.0.1:9222/devtools/browser/abc"]
