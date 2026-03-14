"""Authentication: registration, login, sessions, and FastAPI dependency."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from lazyclaw.config import Config, load_config
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

        await db.execute(
            "INSERT INTO users (id, username, password_hash, encryption_salt, display_name) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, username, pw_hash, salt, display_name),
        )
        await db.commit()

    logger.info("Registered user %s (%s)", username, user_id)
    return User(id=user_id, username=username, display_name=display_name, encryption_salt=salt)


async def authenticate_user(config: Config, username: str, password: str) -> User | None:
    """Verify credentials. Returns User or None."""
    async with db_session(config) as db:
        row = await db.execute(
            "SELECT id, username, password_hash, encryption_salt, display_name "
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
            "SELECT s.user_id, s.expires_at, u.username, u.display_name, u.encryption_salt "
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


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


auth_router = APIRouter(prefix="/api/auth", tags=["auth"])

_is_production = "localhost" not in _config.cors_origin and "127.0.0.1" not in _config.cors_origin


@auth_router.post("/register")
async def register(body: RegisterRequest, response: Response):
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
    return {"id": user.id, "username": user.username, "display_name": user.display_name}


@auth_router.post("/login")
async def login(body: LoginRequest, response: Response):
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
    return {"id": user.id, "username": user.username, "display_name": user.display_name}


@auth_router.post("/logout")
async def logout(request: Request, response: Response):
    session_id = request.cookies.get("session_id")
    if session_id:
        await delete_session(_config, session_id)
    response.delete_cookie("session_id")
    return {"status": "ok"}


@auth_router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "username": user.username, "display_name": user.display_name}
