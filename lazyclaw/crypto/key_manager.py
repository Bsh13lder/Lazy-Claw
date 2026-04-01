"""Envelope encryption key manager.

Manages per-user Data Encryption Keys (DEKs) using the envelope pattern:

    SERVER_SECRET + user_id + per_user_salt
        → PBKDF2 (600K iterations)
        → wrapping_key
        → AES-GCM-wraps DEK
        → stored as ``encrypted_dek`` in users table

    DEK (random 256-bit)
        → encrypts ALL user data (messages, memories, vault, etc.)

Benefits:
- SERVER_SECRET rotation: re-wrap all DEKs (~fast), data stays untouched
- Per-user isolation: each user has a unique random DEK
- Legacy migration: old derived keys become DEKs transparently
- Memory safety: DEKs cleared from cache on logout / TTL expiry
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from lazyclaw.config import Config
from lazyclaw.crypto.encryption import (
    derive_server_key,
    derive_wrapping_key,
    generate_dek,
    secure_zero,
    unwrap_dek,
    wrap_dek,
)
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# In-memory DEK cache with TTL
# ---------------------------------------------------------------------------

_DEK_CACHE_TTL = 3600.0  # 1 hour
_DEK_CACHE_MAX = 128

@dataclass
class _CachedDEK:
    dek: bytearray  # mutable so we can zero it
    created_at: float


_cache: dict[str, _CachedDEK] = {}
_cache_lock = threading.Lock()


def _evict_expired() -> None:
    """Remove expired entries. Must be called with _cache_lock held."""
    now = time.monotonic()
    expired = [
        uid for uid, entry in _cache.items()
        if (now - entry.created_at) >= _DEK_CACHE_TTL
    ]
    for uid in expired:
        entry = _cache.pop(uid)
        secure_zero(entry.dek)


def _cache_dek(user_id: str, dek: bytes) -> bytes:
    """Store a DEK in cache. Returns the same DEK bytes."""
    with _cache_lock:
        _evict_expired()
        # Evict oldest if full
        if len(_cache) >= _DEK_CACHE_MAX and user_id not in _cache:
            oldest_uid = min(_cache, key=lambda k: _cache[k].created_at)
            old = _cache.pop(oldest_uid)
            secure_zero(old.dek)

        # Replace existing entry
        existing = _cache.pop(user_id, None)
        if existing is not None:
            secure_zero(existing.dek)

        _cache[user_id] = _CachedDEK(
            dek=bytearray(dek),
            created_at=time.monotonic(),
        )
    return dek


def _get_cached_dek(user_id: str) -> bytes | None:
    """Get DEK from cache if present and not expired."""
    with _cache_lock:
        entry = _cache.get(user_id)
        if entry is None:
            return None
        if (time.monotonic() - entry.created_at) >= _DEK_CACHE_TTL:
            secure_zero(entry.dek)
            _cache.pop(user_id, None)
            return None
        return bytes(entry.dek)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_user_dek(config: Config, user_id: str) -> bytes:
    """Get the Data Encryption Key for a user.

    1. Returns from cache if available and not expired.
    2. Loads ``encrypted_dek`` from the users table.
    3. If the user has an ``encrypted_dek``, unwrap it with the wrapping key.
    4. If the user has NO ``encrypted_dek`` (legacy), derive the old key,
       adopt it as their DEK, wrap and store it for future use.

    This is the **only** function callers need for encryption operations.
    """
    # Fast path: cache hit
    cached = _get_cached_dek(user_id)
    if cached is not None:
        return cached

    # Load user's salt and wrapped DEK from DB
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT encryption_salt, encrypted_dek FROM users WHERE id = ?",
            (user_id,),
        )
        result = await row.fetchone()

    if not result:
        raise ValueError(f"User {user_id} not found")

    user_salt: str = result[0]
    encrypted_dek_token: str | None = result[1]

    if encrypted_dek_token:
        # Normal path: unwrap existing DEK
        wrapping_key = derive_wrapping_key(
            config.server_secret, user_id, user_salt.encode("utf-8"),
        )
        dek = unwrap_dek(encrypted_dek_token, wrapping_key)
    else:
        # Legacy migration: the old derived key becomes the DEK
        dek = derive_server_key(config.server_secret, user_id)
        await _store_wrapped_dek(config, user_id, user_salt, dek)
        logger.info("Migrated user %s to envelope encryption", user_id)

    return _cache_dek(user_id, dek)


async def create_user_dek(
    config: Config,
    user_id: str,
    user_salt: str,
) -> bytes:
    """Generate and store a new DEK for a freshly registered user.

    Call this during registration, BEFORE any data is encrypted.
    """
    dek = generate_dek()
    await _store_wrapped_dek(config, user_id, user_salt, dek)
    return _cache_dek(user_id, dek)


async def rotate_server_secret(
    config: Config,
    new_secret: str,
) -> int:
    """Re-wrap all user DEKs with a new SERVER_SECRET.

    Steps:
    1. For each user, unwrap their DEK with the OLD server secret.
    2. Re-wrap the DEK with the NEW server secret.
    3. Update the database.

    Returns the number of users re-wrapped.

    IMPORTANT: After calling this, update the SERVER_SECRET environment
    variable and restart the server.
    """
    count = 0
    async with db_session(config) as db:
        rows = await db.execute(
            "SELECT id, encryption_salt, encrypted_dek FROM users"
        )
        users = await rows.fetchall()

    for user_row in users:
        uid = user_row[0]
        salt = user_row[1]
        wrapped = user_row[2]

        if not wrapped:
            # Legacy user without DEK — derive old key, wrap with new secret
            dek = derive_server_key(config.server_secret, uid)
        else:
            # Unwrap with old secret
            old_wrapping = derive_wrapping_key(
                config.server_secret, uid, salt.encode("utf-8"),
            )
            dek = unwrap_dek(wrapped, old_wrapping)

        # Wrap with new secret
        new_wrapping = derive_wrapping_key(
            new_secret, uid, salt.encode("utf-8"),
        )
        new_wrapped = wrap_dek(dek, new_wrapping)

        async with db_session(config) as db:
            await db.execute(
                "UPDATE users SET encrypted_dek = ? WHERE id = ?",
                (new_wrapped, uid),
            )
            await db.commit()

        count += 1

    # Clear all caches — keys derived from old secret are stale
    clear_all_deks()
    logger.info("Rotated server secret: re-wrapped %d user DEKs", count)
    return count


def clear_user_dek(user_id: str) -> None:
    """Remove a user's DEK from cache (call on logout)."""
    with _cache_lock:
        entry = _cache.pop(user_id, None)
        if entry is not None:
            secure_zero(entry.dek)


def clear_all_deks() -> None:
    """Clear all cached DEKs (call on shutdown or secret rotation)."""
    with _cache_lock:
        for entry in _cache.values():
            secure_zero(entry.dek)
        _cache.clear()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _store_wrapped_dek(
    config: Config,
    user_id: str,
    user_salt: str,
    dek: bytes,
) -> None:
    """Wrap a DEK and store it in the users table."""
    wrapping_key = derive_wrapping_key(
        config.server_secret, user_id, user_salt.encode("utf-8"),
    )
    wrapped = wrap_dek(dek, wrapping_key)
    async with db_session(config) as db:
        await db.execute(
            "UPDATE users SET encrypted_dek = ? WHERE id = ?",
            (wrapped, user_id),
        )
        await db.commit()
