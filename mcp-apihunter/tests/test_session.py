"""Cookie-domain scoping + base_url host extraction."""
from __future__ import annotations

import pytest

from mcp_apihunter.session import _domain_matches, cookie_domain_for


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
