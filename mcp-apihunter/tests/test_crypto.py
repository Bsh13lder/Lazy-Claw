"""Crypto round-trip + tamper/isolation properties."""
from __future__ import annotations

import pytest

from mcp_apihunter import crypto


def test_round_trip():
    key = crypto.derive_manifest_key("server-secret", "user-1")
    aad = crypto.manifest_aad("user-1", "himap-admin")
    token = crypto.encrypt('{"hello": "world"}', key, aad)
    assert crypto.is_encrypted(token)
    assert crypto.decrypt(token, key, aad) == '{"hello": "world"}'


def test_key_is_deterministic_per_user():
    a = crypto.derive_manifest_key("s", "user-1")
    b = crypto.derive_manifest_key("s", "user-1")
    c = crypto.derive_manifest_key("s", "user-2")
    assert a == b
    assert a != c


def test_wrong_key_fails():
    aad = crypto.manifest_aad("user-1", "site")
    token = crypto.encrypt("secret", crypto.derive_manifest_key("s", "user-1"), aad)
    with pytest.raises(Exception):
        crypto.decrypt(token, crypto.derive_manifest_key("s", "user-2"), aad)


def test_aad_binds_to_site():
    key = crypto.derive_manifest_key("s", "user-1")
    token = crypto.encrypt("secret", key, crypto.manifest_aad("user-1", "site-a"))
    # Same key, different site AAD → authentication fails.
    with pytest.raises(Exception):
        crypto.decrypt(token, key, crypto.manifest_aad("user-1", "site-b"))


def test_missing_inputs_raise():
    with pytest.raises(ValueError):
        crypto.derive_manifest_key("", "user-1")
    with pytest.raises(ValueError):
        crypto.derive_manifest_key("s", "")
