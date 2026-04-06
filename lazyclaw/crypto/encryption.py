"""Core encryption primitives for LazyClaw.

Provides AES-256-GCM encryption with PBKDF2 key derivation, supporting both
legacy v1 format and hardened v2 format with AAD binding.

Security properties:
- AES-256-GCM authenticated encryption
- PBKDF2-SHA256 key derivation (600K iterations, OWASP 2024)
- Per-user salt for key derivation
- Additional Authenticated Data (AAD) prevents ciphertext swapping
- Envelope encryption (DEK pattern) enables key rotation
"""

from __future__ import annotations

import base64
import ctypes
import logging
import os
import threading
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FIXED_SALT = b"lazyclaw-server-key-v1"  # Legacy only — do NOT use for new keys

ITERATIONS_V1 = 100_000   # Legacy (backward compat)
ITERATIONS_V2 = 600_000   # OWASP 2024 recommendation

_DEK_BYTES = 32  # 256-bit Data Encryption Key
_NONCE_BYTES = 12  # 96-bit nonce for AES-GCM
_WRAPPING_TAG = b"dek-wrap-v1"  # AAD for DEK wrapping


# ---------------------------------------------------------------------------
# Memory safety
# ---------------------------------------------------------------------------

def secure_zero(buf: bytearray | memoryview) -> None:
    """Best-effort zeroing of a mutable buffer.

    Python's garbage collector doesn't guarantee immediate collection of
    ``bytes`` objects, and ``bytes`` is immutable so it can't be zeroed.
    Callers that need zeroing should use ``bytearray`` and call this function
    when done.  For ``bytes`` returned by the cryptography library we
    at least overwrite the reference (caller must drop their own ref too).
    """
    if isinstance(buf, (bytearray, memoryview)):
        n = len(buf)
        ctypes.memset(ctypes.addressof((ctypes.c_char * n).from_buffer(buf)), 0, n)


def _to_zeroable(key: bytes) -> bytearray:
    """Copy an immutable ``bytes`` key into a mutable ``bytearray``."""
    return bytearray(key)


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------

def derive_key(
    password: str,
    salt: bytes,
    iterations: int = ITERATIONS_V2,
) -> bytes:
    """Derive a 256-bit AES key from a password via PBKDF2-HMAC-SHA256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(password.encode("utf-8"))


def derive_wrapping_key(
    server_secret: str,
    user_id: str,
    user_salt: bytes,
) -> bytes:
    """Derive a key for wrapping/unwrapping a user's DEK.

    Uses the per-user salt (from registration) and 600K iterations.
    """
    return derive_key(
        password=server_secret + user_id,
        salt=user_salt,
        iterations=ITERATIONS_V2,
    )


# ---------------------------------------------------------------------------
# Legacy key derivation (v1 backward compat — will be removed)
# ---------------------------------------------------------------------------

@dataclass
class _CachedKey:
    key: bytes
    created_at: float


_legacy_cache: dict[tuple[str, str], _CachedKey] = {}
_legacy_cache_lock = threading.Lock()
_LEGACY_CACHE_MAX = 64
_LEGACY_CACHE_TTL = 3600.0  # 1 hour


def derive_server_key(server_secret: str, user_id: str) -> bytes:
    """Derive AES-256 key for server-side encryption (LEGACY).

    Kept for backward compatibility with v1 encrypted data.
    New code should use the DEK pattern via ``key_manager``.

    Uses a TTL-bounded cache (1 hour, 64 entries max) instead of the
    previous unbounded ``functools.lru_cache``.
    """
    cache_key = (server_secret, user_id)
    now = time.monotonic()

    with _legacy_cache_lock:
        entry = _legacy_cache.get(cache_key)
        if entry is not None and (now - entry.created_at) < _LEGACY_CACHE_TTL:
            return entry.key

    # Derive outside the lock (expensive CPU work)
    key = derive_key(server_secret + user_id, FIXED_SALT, ITERATIONS_V1)

    with _legacy_cache_lock:
        # Evict expired entries if cache is full
        if len(_legacy_cache) >= _LEGACY_CACHE_MAX:
            expired = [
                k for k, v in _legacy_cache.items()
                if (now - v.created_at) >= _LEGACY_CACHE_TTL
            ]
            for k in expired:
                _legacy_cache.pop(k, None)
            # If still full after eviction, drop oldest
            if len(_legacy_cache) >= _LEGACY_CACHE_MAX:
                oldest_key = min(_legacy_cache, key=lambda k: _legacy_cache[k].created_at)
                _legacy_cache.pop(oldest_key, None)

        _legacy_cache[cache_key] = _CachedKey(key=key, created_at=now)

    return key


def clear_legacy_cache() -> None:
    """Clear all cached legacy keys (call on server secret rotation)."""
    with _legacy_cache_lock:
        _legacy_cache.clear()


def evict_legacy_cache(user_id: str) -> None:
    """Remove a specific user's cached key (call on logout)."""
    with _legacy_cache_lock:
        to_remove = [k for k in _legacy_cache if k[1] == user_id]
        for k in to_remove:
            _legacy_cache.pop(k, None)


# ---------------------------------------------------------------------------
# DEK (Data Encryption Key) wrapping — envelope encryption
# ---------------------------------------------------------------------------

def generate_dek() -> bytes:
    """Generate a random 256-bit Data Encryption Key."""
    return os.urandom(_DEK_BYTES)


def wrap_dek(dek: bytes, wrapping_key: bytes) -> str:
    """Encrypt a DEK with the wrapping key. Returns a storable string.

    Format: ``wrapped:v1:{base64_nonce}:{base64_ciphertext}``
    Uses AAD to bind the wrapping to its purpose.
    """
    nonce = os.urandom(_NONCE_BYTES)
    aesgcm = AESGCM(wrapping_key)
    ciphertext = aesgcm.encrypt(nonce, dek, _WRAPPING_TAG)
    b64_nonce = base64.b64encode(nonce).decode("ascii")
    b64_ct = base64.b64encode(ciphertext).decode("ascii")
    return f"wrapped:v1:{b64_nonce}:{b64_ct}"


def unwrap_dek(token: str, wrapping_key: bytes) -> bytes:
    """Decrypt a wrapped DEK. Returns raw key bytes."""
    parts = token.split(":", 3)
    if len(parts) != 4 or parts[0] != "wrapped" or parts[1] != "v1":
        raise ValueError("Invalid wrapped DEK format")
    nonce = base64.b64decode(parts[2])
    ciphertext = base64.b64decode(parts[3])
    aesgcm = AESGCM(wrapping_key)
    return aesgcm.decrypt(nonce, ciphertext, _WRAPPING_TAG)


# ---------------------------------------------------------------------------
# Encryption / decryption — v2 with AAD
# ---------------------------------------------------------------------------

def encrypt(plaintext: str, key: bytes, aad: bytes | None = None) -> str:
    """Encrypt a string with AES-256-GCM.

    If ``aad`` is provided, produces a v2 token; otherwise v1 for
    backward compatibility.

    Format v1: ``enc:v1:{nonce}:{ciphertext}``
    Format v2: ``enc:v2:{nonce}:{ciphertext}``
    """
    nonce = os.urandom(_NONCE_BYTES)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), aad)
    b64_nonce = base64.b64encode(nonce).decode("ascii")
    b64_ct = base64.b64encode(ciphertext).decode("ascii")
    version = "v2" if aad is not None else "v1"
    return f"enc:{version}:{b64_nonce}:{b64_ct}"


def decrypt(token: str, key: bytes, aad: bytes | None = None) -> str:
    """Decrypt an AES-256-GCM token.

    For v1 tokens, ``aad`` is ignored (v1 was encrypted without AAD).
    For v2 tokens, ``aad`` must match what was used at encryption time.
    """
    parts = token.split(":", 3)
    if len(parts) != 4 or parts[0] != "enc":
        raise ValueError("Invalid encrypted token format")

    version = parts[1]
    if version not in ("v1", "v2"):
        raise ValueError(f"Unknown encryption version: {version}")

    nonce = base64.b64decode(parts[2])
    ciphertext = base64.b64decode(parts[3])
    aesgcm = AESGCM(key)

    # v1 was always encrypted without AAD
    effective_aad = aad if version == "v2" else None
    plaintext = aesgcm.decrypt(nonce, ciphertext, effective_aad)
    return plaintext.decode("utf-8")


def try_decrypt(
    token: str,
    key: bytes,
    aad: bytes | None = None,
    fallback: str = "[encrypted]",
) -> str:
    """Decrypt, returning *fallback* on any failure (wrong key, corruption, etc.)."""
    try:
        return decrypt(token, key, aad)
    except Exception:
        logger.warning("Decryption failed for token prefix %.20s…", token[:20], exc_info=True)
        return fallback


def is_encrypted(value: str) -> bool:
    """Check if a value is in encrypted format (v1 or v2)."""
    return value.startswith("enc:v1:") or value.startswith("enc:v2:")


def encrypt_field(value: str | None, key: bytes, aad: bytes | None = None) -> str | None:
    """Null-safe encryption wrapper."""
    if value is None:
        return None
    return encrypt(value, key, aad)


def decrypt_field(
    value: str | None,
    key: bytes,
    aad: bytes | None = None,
    fallback: str = "[encrypted]",
) -> str | None:
    """Null-safe, error-safe decryption wrapper."""
    if value is None:
        return None
    if not is_encrypted(value):
        return value
    return try_decrypt(value, key, aad, fallback=fallback)


# ---------------------------------------------------------------------------
# AAD helpers
# ---------------------------------------------------------------------------

def user_aad(user_id: str, context: str = "") -> bytes:
    """Build AAD bytes for user-scoped encryption.

    Binds ciphertext to a specific user (and optionally a table/field),
    preventing an attacker from swapping encrypted values between users
    or between fields.
    """
    tag = f"user:{user_id}"
    if context:
        tag += f":{context}"
    return tag.encode("utf-8")
