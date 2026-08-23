"""Manifest model validation, immutable updates, and encrypted store."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from mcp_apihunter.manifest import (
    CsrfSpec,
    Endpoint,
    Manifest,
    ManifestStore,
    slug_suggestion,
)


def _ep(name="post_blog", **kw):
    base = dict(name=name, method="POST", url_template="/api/posts/")
    base.update(kw)
    return Endpoint(**base)


def test_endpoint_normalizes_method():
    assert _ep(method="post").method == "POST"


def test_endpoint_rejects_bad_name():
    with pytest.raises(ValidationError):
        _ep(name="Bad Name")


def test_endpoint_rejects_bad_method():
    with pytest.raises(ValidationError):
        _ep(method="FETCH")


def test_manifest_rejects_bad_base_url():
    with pytest.raises(ValidationError):
        Manifest(site="s", base_url="ftp://x")


def test_manifest_strips_trailing_slash():
    m = Manifest(site="himap-admin", base_url="https://himap.co/")
    assert m.base_url == "https://himap.co"


def test_with_endpoint_is_immutable():
    m = Manifest(site="s", base_url="https://x.com")
    m2 = m.with_endpoint(_ep())
    assert m.endpoints == []          # original untouched
    assert len(m2.endpoints) == 1
    assert m2.version == m.version + 1


def test_with_endpoint_replaces_by_name():
    m = Manifest(site="s", base_url="https://x.com").with_endpoint(_ep(description="v1"))
    m = m.with_endpoint(_ep(description="v2"))
    assert len(m.endpoints) == 1
    assert m.get("post_blog").description == "v2"


def test_without_endpoint():
    m = Manifest(site="s", base_url="https://x.com").with_endpoint(_ep())
    m = m.without_endpoint("post_blog")
    assert m.get("post_blog") is None


def test_store_round_trip(tmp_path):
    store = ManifestStore(tmp_path, "server-secret", "user-1")
    m = Manifest(
        site="himap-admin",
        base_url="https://himap.co",
        login_url="https://himap.co/admin/login/",
    ).with_endpoint(_ep(csrf=CsrfSpec(source="cookie"), mutating=True))
    store.save(m)

    loaded = store.load("himap-admin")
    assert loaded is not None
    assert loaded.base_url == "https://himap.co"
    assert loaded.get("post_blog").mutating is True
    assert loaded.get("post_blog").csrf.source == "cookie"


def test_store_encrypts_on_disk(tmp_path):
    store = ManifestStore(tmp_path, "server-secret", "user-1")
    store.save(Manifest(site="s", base_url="https://secret.example"))
    raw = (tmp_path / "apihunter" / "s.json.enc").read_text()
    assert raw.startswith("enc:v2:")
    assert "secret.example" not in raw


def test_store_list_and_delete(tmp_path):
    store = ManifestStore(tmp_path, "s", "user-1")
    store.save(Manifest(site="a", base_url="https://a.com"))
    store.save(Manifest(site="b", base_url="https://b.com"))
    assert store.list_sites() == ["a", "b"]
    assert store.delete("a") is True
    assert store.delete("a") is False
    assert store.list_sites() == ["b"]


def test_store_isolates_users(tmp_path):
    ManifestStore(tmp_path, "s", "user-1").save(Manifest(site="s", base_url="https://x.com"))
    # A different user's key cannot read the file.
    with pytest.raises(Exception):
        ManifestStore(tmp_path, "s", "user-2").load("s")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("himap.co", "himap"),
        ("https://himap.co/admin", "himap"),
        ("HiMap.CO", "himap"),
        ("http://user:pw@shop.example.com:8443/wp-admin", "shop"),
        ("already-valid", None),   # a valid slug needs no suggestion
        ("!!!", None),             # nothing usable can be derived
        ("", None),
        ("http://", None),         # scheme with an empty host derives nothing
        ("-abc", "abc"),           # leading punctuation is stripped
        ("a" * 70, "a" * 64),      # over-long label truncates to a valid slug
    ],
)
def test_slug_suggestion(raw, expected):
    assert slug_suggestion(raw) == expected


def test_path_error_suggests_slug_for_domain(tmp_path):
    store = ManifestStore(tmp_path, "s", "user-1")
    with pytest.raises(ValueError, match=r"try 'himap'"):
        store.load("himap.co")


def test_manifest_model_error_suggests_slug():
    with pytest.raises(ValidationError, match=r"try 'himap'"):
        Manifest(site="himap.co", base_url="https://himap.co")


def test_invalid_slug_without_suggestion_has_no_hint(tmp_path):
    store = ManifestStore(tmp_path, "s", "user-1")
    with pytest.raises(ValueError) as exc:
        store.load("!!!")
    assert "try '" not in str(exc.value)
