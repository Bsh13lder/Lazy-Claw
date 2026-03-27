"""Vault API — encrypted credential management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lazyclaw.config import load_config
from lazyclaw.crypto.vault import (
    delete_credential,
    get_credential,
    list_credentials,
    set_credential,
)
from lazyclaw.gateway.auth import User, _RateLimiter, get_current_user

_config = load_config()

router = APIRouter(prefix="/api/vault", tags=["vault"])

_vault_limiter = _RateLimiter(max_requests=30, window_seconds=60)


def _check_vault_rate_limit(user_id: str) -> None:
    """Raise 429 if user exceeds vault rate limit."""
    if not _vault_limiter.check(user_id):
        raise HTTPException(
            status_code=429,
            detail="Too many vault requests. Try again later.",
        )


class VaultSetRequest(BaseModel):
    value: str


@router.get("")
async def list_vault_keys(user: User = Depends(get_current_user)):
    """List credential key names (not values)."""
    _check_vault_rate_limit(user.id)
    keys = await list_credentials(_config, user.id)
    return {"keys": keys}


@router.put("/{key}")
async def set_vault_credential(
    key: str,
    body: VaultSetRequest,
    user: User = Depends(get_current_user),
):
    """Set or update an encrypted credential."""
    _check_vault_rate_limit(user.id)
    await set_credential(_config, user.id, key, body.value)
    return {"status": "ok", "key": key}


@router.delete("/{key}")
async def delete_vault_credential(key: str, user: User = Depends(get_current_user)):
    """Delete a credential."""
    _check_vault_rate_limit(user.id)
    deleted = await delete_credential(_config, user.id, key)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Credential '{key}' not found")
    return {"status": "deleted", "key": key}
