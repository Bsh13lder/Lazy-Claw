"""Recovery phrase API routes.

Endpoints:
  POST /api/auth/generate-recovery  — generate/regenerate phrase (auth required)
  POST /api/auth/recover            — recover account with phrase + set new password
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lazyclaw.config import load_config
from lazyclaw.gateway.auth import (
    User,
    _RateLimiter,
    create_session,
    generate_recovery_for_user,
    get_current_user,
    recover_account,
)

logger = logging.getLogger(__name__)

_config = load_config()

router = APIRouter(prefix="/api/auth", tags=["auth"])

_recovery_gen_limiter = _RateLimiter(max_requests=3, window_seconds=3600)
_recovery_use_limiter = _RateLimiter(max_requests=5, window_seconds=3600)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class GenerateRecoveryRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class RecoverAccountRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    recovery_phrase: str = Field(min_length=10, max_length=512)
    new_password: str = Field(min_length=8, max_length=128)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/generate-recovery")
async def generate_recovery(
    body: GenerateRecoveryRequest,
    user: User = Depends(get_current_user),
):
    """Generate (or regenerate) a recovery phrase for the authenticated user.

    Requires current password. Returns the phrase ONCE — it is never stored
    in plaintext and cannot be retrieved again after this response.
    """
    if not _recovery_gen_limiter.check(user.id):
        raise HTTPException(
            status_code=429,
            detail="Too many recovery generation attempts. Try again later.",
        )

    try:
        phrase = await generate_recovery_for_user(_config, user.id, body.password)
    except PermissionError:
        raise HTTPException(status_code=401, detail="Invalid password")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "recovery_phrase": phrase,
        "notice": (
            "IMPORTANT: Store this phrase somewhere safe and offline. "
            "It will NEVER be shown again. Use it to recover your account "
            "if you forget your password."
        ),
    }


@router.post("/recover")
async def recover(body: RecoverAccountRequest):
    """Recover an account using a recovery phrase and set a new password.

    This endpoint does NOT require an active session — it is for users who
    have lost their password. Rate-limited to prevent brute force.
    """
    # Rate-limit by username to prevent enumeration
    if not _recovery_use_limiter.check(body.username):
        raise HTTPException(
            status_code=429,
            detail="Too many recovery attempts. Try again later.",
        )

    try:
        user = await recover_account(
            _config,
            body.username,
            body.recovery_phrase,
            body.new_password,
        )
    except ValueError as exc:
        # Return a generic error to prevent user enumeration
        logger.warning("Recovery attempt failed for username=%s: %s", body.username, exc)
        raise HTTPException(
            status_code=400,
            detail="Recovery failed. Check your username and recovery phrase.",
        )

    logger.info("Account recovered: %s", body.username)
    return {
        "status": "ok",
        "message": "Password updated. You can now log in with your new password.",
        "username": user.username,
    }
