"""Encrypted OAuth token storage via LazyClaw's credential vault.

Tokens stored as:
  vault key: "mcp_oauth:{server_name}"
  vault value: JSON {access_token, refresh_token, expires_at, scope, metadata_url, ...}

All encrypted with AES-256-GCM per user (same as all vault data).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)

VAULT_PREFIX = "mcp_oauth:"


@dataclass(frozen=True)
class OAuthTokenData:
    """Immutable OAuth token bundle persisted in the vault."""

    access_token: str
    refresh_token: str | None
    expires_at: float  # time.time() epoch
    scope: str
    metadata_url: str  # resource_metadata URL for re-discovery
    token_endpoint: str  # for silent refresh without re-discovery
    client_id: str = "lazyclaw"  # may be dynamic from RFC 7591 registration


def is_token_expired(data: OAuthTokenData, buffer_seconds: int = 60) -> bool:
    """Check if a token is expired or will expire within the buffer."""
    return time.time() >= (data.expires_at - buffer_seconds)


async def save_tokens(
    config, user_id: str, server_name: str, data: OAuthTokenData,
) -> None:
    """Store OAuth tokens encrypted in vault."""
    from lazyclaw.crypto.vault import set_credential

    payload = json.dumps(asdict(data))
    await set_credential(config, user_id, f"{VAULT_PREFIX}{server_name}", payload)
    logger.info("OAuth tokens saved for server %s", server_name)


async def load_tokens(
    config, user_id: str, server_name: str,
) -> OAuthTokenData | None:
    """Load OAuth tokens from vault. Returns None if not found or corrupted."""
    from lazyclaw.crypto.vault import get_credential

    raw = await get_credential(config, user_id, f"{VAULT_PREFIX}{server_name}")
    if not raw:
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Corrupted OAuth token data for %s — ignoring", server_name)
        return None

    try:
        return OAuthTokenData(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=data.get("expires_at", 0.0),
            scope=data.get("scope", ""),
            metadata_url=data.get("metadata_url", ""),
            token_endpoint=data.get("token_endpoint", ""),
            client_id=data.get("client_id", "lazyclaw"),
        )
    except (KeyError, TypeError) as exc:
        logger.warning("Invalid OAuth token data for %s: %s", server_name, exc)
        return None


async def delete_tokens(config, user_id: str, server_name: str) -> bool:
    """Remove stored tokens (on disconnect or revoke)."""
    from lazyclaw.crypto.vault import delete_credential

    deleted = await delete_credential(
        config, user_id, f"{VAULT_PREFIX}{server_name}",
    )
    if deleted:
        logger.info("OAuth tokens deleted for server %s", server_name)
    return deleted
