"""Persistent Claude-subscription OAuth token store + auth status.

The SDK/CLI transports authenticate against the user's Claude subscription
($0 within plan limits). There are two credential sources, in precedence
order:

  1. ``CLAUDE_CODE_OAUTH_TOKEN`` — a long-lived (~1 year) token minted by
     ``claude setup-token``. Stored HERE (chmod 600) in the persistent data
     dir and injected into the SDK subprocess env by ``claude_sdk_provider``.
     This is the self-service path: paste once in web Settings → survives
     rebuilds, no bi-weekly expiry, no interactive login inside the container.
  2. ``~/.claude/.credentials.json`` — the short-lived OAuth access token
     from ``claude /login``. Expires ~2 weeks and the container can't refresh
     it non-interactively, so it's the fallback, not the primary.

Kept deliberately dependency-free (no Config import) so the provider can read
the token at chat time without threading config through every call. The path
mirrors ``config.py``'s ``database_dir`` resolution (``DATABASE_DIR`` env or
``./data``) so it lands on the same persistent bind mount as the DB.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_TOKEN_FILENAME = ".claude_oauth_token"
_CREDENTIALS_PATH = "~/.claude/.credentials.json"


def _data_dir() -> Path:
    """Persistent data dir — mirrors config.py (DATABASE_DIR env or ./data)."""
    return Path(os.getenv("DATABASE_DIR", "./data")).resolve()


def _token_file() -> Path:
    return _data_dir() / _TOKEN_FILENAME


def read_claude_oauth_token() -> str | None:
    """Return the stored setup-token, or ``None``. Never raises."""
    try:
        path = _token_file()
        if not path.is_file():
            return None
        token = path.read_text(encoding="utf-8").strip()
        return token or None
    except Exception:
        logger.warning("Failed reading Claude OAuth token", exc_info=True)
        return None


def write_claude_oauth_token(token: str) -> None:
    """Persist a setup-token (chmod 600). An empty string clears it."""
    token = (token or "").strip()
    if not token:
        clear_claude_oauth_token()
        return
    path = _token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        logger.warning("Could not chmod 600 the Claude token file", exc_info=True)


def clear_claude_oauth_token() -> None:
    """Remove the stored setup-token so auth falls back to .credentials.json."""
    try:
        _token_file().unlink(missing_ok=True)
    except Exception:
        logger.warning("Failed clearing Claude OAuth token", exc_info=True)


def _credentials_expiry() -> tuple[bool, str | None]:
    """Best-effort ``(present_and_unexpired, iso_expiry)`` from the login file."""
    try:
        cred = Path(os.path.expanduser(_CREDENTIALS_PATH))
        if not cred.is_file() or cred.stat().st_size == 0:
            return False, None
        data = json.loads(cred.read_text(encoding="utf-8"))
        oauth = data.get("claudeAiOauth") or {}
        exp_ms = oauth.get("expiresAt")
        if not isinstance(exp_ms, (int, float)):
            return True, None  # present but no parseable expiry — trust it
        iso = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(exp_ms / 1000))
        return (time.time() < exp_ms / 1000), iso
    except Exception:
        logger.warning("Failed parsing Claude credentials expiry", exc_info=True)
        return False, None


def claude_auth_status() -> dict:
    """Cheap, no-network status for the web Settings badge.

    Precedence mirrors the provider: a stored setup-token wins (long-lived,
    can't be cheaply validated offline — assumed good until a live ping fails);
    otherwise the ``claude /login`` credentials file and its expiry.
    """
    if read_claude_oauth_token():
        return {
            "authenticated": True,
            "source": "token",
            "detail": "Using a saved setup-token (long-lived, ~1 year).",
            "expires_at": None,
        }

    valid, iso = _credentials_expiry()
    if iso is None and not valid:
        return {
            "authenticated": False,
            "source": "none",
            "detail": "Not logged in. Paste a `claude setup-token` below.",
            "expires_at": None,
        }
    if valid:
        return {
            "authenticated": True,
            "source": "credentials",
            "detail": f"Logged in via `claude /login` (expires {iso}).",
            "expires_at": iso,
        }
    return {
        "authenticated": False,
        "source": "credentials",
        "detail": f"Login token EXPIRED on {iso}. Paste a setup-token below.",
        "expires_at": iso,
    }
