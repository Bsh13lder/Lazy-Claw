"""Smoke tests for the encryption core.

LazyClaw's pitch — "all user content encrypted at rest with AES-256-GCM" —
lives or dies on these primitives. If any of these fail, the security
story in README.md is not true.
"""

from __future__ import annotations

import secrets

import pytest

from lazyclaw.crypto.encryption import (
    decrypt,
    decrypt_field,
    encrypt,
    encrypt_field,
    is_encrypted,
)


@pytest.fixture
def key() -> bytes:
    # 32 bytes = 256 bits = AES-256 key size
    return secrets.token_bytes(32)


def test_encrypt_decrypt_roundtrip(key: bytes) -> None:
    plaintext = "hello world — with émoji 🔐 and unicode ñ"
    ct = encrypt(plaintext, key)
    assert ct.startswith("enc:"), "encrypted payload should use the enc: prefix"
    assert plaintext not in ct, "plaintext must not leak in the ciphertext"
    assert decrypt(ct, key) == plaintext


def test_encrypt_is_non_deterministic(key: bytes) -> None:
    """Same input + same key must produce different ciphertexts (unique nonce).

    Without this property, an attacker who observes two identical
    encryptions knows the plaintexts match — a real privacy leak.
    """
    plaintext = "dgt-appointment-id-1234"
    a = encrypt(plaintext, key)
    b = encrypt(plaintext, key)
    assert a != b, "two encryptions of the same plaintext must differ (nonce)"
    assert decrypt(a, key) == plaintext
    assert decrypt(b, key) == plaintext


def test_tampered_ciphertext_is_rejected(key: bytes) -> None:
    plaintext = "sensitive-data"
    ct = encrypt(plaintext, key)
    # Flip the final character of the base64 payload. AES-GCM's auth
    # tag should detect this and refuse to decrypt.
    tampered = ct[:-1] + ("A" if ct[-1] != "A" else "B")
    with pytest.raises(Exception):
        decrypt(tampered, key)


def test_wrong_key_is_rejected(key: bytes) -> None:
    plaintext = "user-NIE-number"
    ct = encrypt(plaintext, key)
    wrong_key = secrets.token_bytes(32)
    with pytest.raises(Exception):
        decrypt(ct, wrong_key)


def test_is_encrypted_detection() -> None:
    assert is_encrypted("enc:v1:aGVsbG8=:d29ybGQ=") is True
    assert is_encrypted("enc:v2:aGVsbG8=:d29ybGQ=") is True
    assert is_encrypted("hello world") is False
    assert is_encrypted("") is False


def test_field_helpers_handle_none(key: bytes) -> None:
    """encrypt_field / decrypt_field pass None through untouched.

    DB rows often have NULL columns; the helpers must not blow up on them.
    """
    assert encrypt_field(None, key) is None
    assert decrypt_field(None, key) is None


def test_field_helpers_roundtrip(key: bytes) -> None:
    assert decrypt_field(encrypt_field("task description", key), key) == "task description"
