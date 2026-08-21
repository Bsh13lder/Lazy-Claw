"""Capture → endpoint-skeleton heuristics."""
from __future__ import annotations

from mcp_apihunter.discovery import (
    _endpoint_name,
    _parameterize_path,
    endpoints_from_capture,
)


def _rec(url, method="GET", status=200, mime="application/json", **kw):
    base = dict(url=url, method=method, status=status, mime_type=mime,
                failed=False, from_cache=False)
    base.update(kw)
    return base


def test_parameterize_numeric_and_uuid():
    tpl, names = _parameterize_path("/api/posts/42/comments/7/")
    assert tpl == "/api/posts/{id}/comments/{id2}/"
    assert names == ["id", "id2"]
    tpl2, _ = _parameterize_path("/api/u/f47ac10b-58cc-4372-a567-0e02b2c3d479/")
    assert tpl2 == "/api/u/{id}/"


def test_endpoint_name():
    assert _endpoint_name("GET", "/api/posts/") == "get_api_posts"
    assert _endpoint_name("POST", "/api/posts/") == "create_api_posts"
    assert _endpoint_name("DELETE", "/api/posts/{id}/") == "delete_api_posts"


def test_filters_assets_and_third_party():
    records = [
        _rec("https://himap.co/api/posts/", mime="application/json"),
        _rec("https://himap.co/static/app.js", mime="application/javascript"),
        _rec("https://himap.co/logo.png", mime="image/png"),
        _rec("https://www.google-analytics.com/collect", mime="application/json"),
        _rec("https://himap.co/style.css", mime="text/css"),
    ]
    eps = endpoints_from_capture(records, "https://himap.co")
    assert [e.url_template for e in eps] == ["/api/posts/"]


def test_skips_failures_and_errors():
    records = [
        _rec("https://himap.co/api/ok/", status=200),
        _rec("https://himap.co/api/bad/", status=500),
        _rec("https://himap.co/api/failed/", failed=True),
        _rec("https://himap.co/api/redirect/", status=302),
    ]
    eps = endpoints_from_capture(records, "https://himap.co")
    assert [e.url_template for e in eps] == ["/api/ok/"]


def test_mutating_gets_csrf_and_json_body():
    records = [_rec("https://himap.co/api/posts/", method="POST", status=201)]
    eps = endpoints_from_capture(records, "https://himap.co")
    ep = eps[0]
    assert ep.mutating is True
    assert ep.body_type == "json"
    assert ep.csrf.source == "cookie"
    assert ep.csrf.target_name == "X-CSRFToken"


def test_dedupes_by_method_and_template():
    records = [
        _rec("https://himap.co/api/posts/1/"),
        _rec("https://himap.co/api/posts/2/"),
        _rec("https://himap.co/api/posts/3/"),
    ]
    eps = endpoints_from_capture(records, "https://himap.co")
    assert len(eps) == 1
    assert eps[0].url_template == "/api/posts/{id}/"


def test_subdomain_is_same_site():
    records = [_rec("https://admin.himap.co/api/x/")]
    eps = endpoints_from_capture(records, "https://himap.co")
    assert len(eps) == 1
