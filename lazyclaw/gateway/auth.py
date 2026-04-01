"""Authentication: registration, login, sessions, and FastAPI dependency."""

from __future__ import annotations

import logging
import os
import secrets
import time as _time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from lazyclaw.config import Config, load_config
from lazyclaw.crypto.key_manager import clear_user_dek, create_user_dek
from lazyclaw.db.connection import db_session

logger = logging.getLogger(__name__)

_config = load_config()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class User:
    id: str
    username: str
    display_name: str | None
    encryption_salt: str
    role: str = "user"


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify password against stored hash (bcrypt or legacy sha256)."""
    if stored_hash.startswith("$2"):
        return bcrypt.checkpw(password.encode(), stored_hash.encode())
    # Legacy sha256 from setup wizard — hash is hex digest of random bytes,
    # not actually derived from a user password. Reject login for these.
    return False


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

async def register_user(
    config: Config,
    username: str,
    password: str,
    display_name: str | None = None,
) -> User:
    """Create a new user with bcrypt hash and random encryption_salt."""
    user_id = str(uuid4())
    salt = secrets.token_urlsafe(16)
    pw_hash = hash_password(password)

    async with db_session(config) as db:
        existing = await db.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        )
        if await existing.fetchone():
            raise ValueError(f"Username '{username}' already taken")

        # First user gets admin role
        count_row = await db.execute("SELECT COUNT(*) FROM users")
        count_result = await count_row.fetchone()
        role = "admin" if count_result and count_result[0] == 0 else "user"

        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt, display_name, role) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, username, pw_hash, salt, display_name, role),
        )
        await db.commit()

    # Generate and store encrypted DEK (envelope encryption)
    await create_user_dek(config, user_id, salt)

    logger.info("Registered user %s (%s) with role %s", username, user_id, role)
    return User(id=user_id, username=username, display_name=display_name, encryption_salt=salt, role=role)


async def authenticate_user(config: Config, username: str, password: str) -> User | None:
    """Verify credentials. Returns User or None."""
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT id, username, password_hash, encryption_salt, display_name, role "
            "FROM users WHERE username = ?",
            (username,),
        )
        result = await row.fetchone()

    if not result:
        return None

    if not verify_password(password, result[2]):
        return None

    return User(
        id=result[0],
        username=result[1],
        display_name=result[4],
        encryption_salt=result[3],
        role=result[5],
    )


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

async def create_session(config: Config, user_id: str, expires_hours: int = 720) -> str:
    """Create a session row, return session_id token."""
    session_id = str(uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=expires_hours)).isoformat()

    async with db_session(config) as db:
        await db.execute(
            "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
            (session_id, user_id, expires_at),
        )
        await db.commit()

    return session_id


async def get_session_user(config: Config, session_id: str) -> User | None:
    """Look up session, check expiry, return User or None."""
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT s.user_id, s.expires_at, u.username, u.display_name, u.encryption_salt, u.role "
            "FROM sessions s JOIN users u ON s.user_id = u.id "
            "WHERE s.id = ?",
            (session_id,),
        )
        result = await row.fetchone()

    if not result:
        return None

    expires_at = datetime.fromisoformat(result[1])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if datetime.now(timezone.utc) > expires_at:
        return None

    return User(
        id=result[0],
        username=result[2],
        display_name=result[3],
        encryption_salt=result[4],
        role=result[5],
    )


async def delete_session(config: Config, session_id: str) -> None:
    """Delete a session row."""
    async with db_session(config) as db:
        await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await db.commit()


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

async def get_current_user(request: Request) -> User:
    """Extract session_id from cookie, resolve user. Raises 401 on failure."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = await get_session_user(_config, session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """FastAPI dependency that requires admin role."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class _RateLimiter:
    """Simple in-memory per-key rate limiter."""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> bool:
        """Returns True if request is allowed, False if rate-limited."""
        now = _time.monotonic()
        timestamps = self._requests[key]
        self._requests[key] = [t for t in timestamps if now - t < self._window]
        if len(self._requests[key]) >= self._max:
            return False
        self._requests[key].append(now)
        return True


_login_limiter = _RateLimiter(max_requests=5, window_seconds=60)
_register_limiter = _RateLimiter(max_requests=3, window_seconds=3600)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)
    invite_token: str | None = None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


auth_router = APIRouter(prefix="/api/auth", tags=["auth"])

_is_production = "localhost" not in _config.cors_origin and "127.0.0.1" not in _config.cors_origin


@auth_router.post("/register")
async def register(body: RegisterRequest, request: Request, response: Response):
    client_ip = request.client.host if request.client else "unknown"
    if not _register_limiter.check(client_ip):
        raise HTTPException(status_code=429, detail="Too many registration attempts. Try again later.")

    # After first user, require invite token
    async with db_session(_config) as db:
        count_row = await db.execute("SELECT COUNT(*) FROM users")
        count_result = await count_row.fetchone()
        user_count = count_result[0] if count_result else 0

    if user_count > 0:
        expected_token = os.getenv("REGISTRATION_TOKEN")
        if not expected_token or body.invite_token != expected_token:
            raise HTTPException(
                status_code=403,
                detail="Registration is invite-only. Provide a valid invite_token.",
            )

    try:
        user = await register_user(_config, body.username, body.password, body.display_name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    session_id = await create_session(_config, user.id)
    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        secure=_is_production,
        samesite="lax",
        max_age=720 * 3600,
    )
    return {"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role}


@auth_router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response):
    client_ip = request.client.host if request.client else "unknown"
    if not _login_limiter.check(client_ip):
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")

    user = await authenticate_user(_config, body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    session_id = await create_session(_config, user.id)
    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        secure=_is_production,
        samesite="lax",
        max_age=720 * 3600,
    )
    return {"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role}


@auth_router.post("/logout")
async def logout(request: Request, response: Response):
    session_id = request.cookies.get("session_id")
    if session_id:
        # Clear cached DEK before destroying session
        user = await get_session_user(_config, session_id)
        if user:
            clear_user_dek(user.id)
        await delete_session(_config, session_id)
    response.delete_cookie(
        "session_id",
        httponly=True,
        secure=_is_production,
        samesite="lax",
    )
    return {"status": "ok"}


@auth_router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role}
