"""Claude subscription re-authentication from the Web UI.

The OAuth token behind MODE_CLAUDE expires (~60 days) and the stored
credential can carry an EMPTY refresh token, in which case the CLI
cannot renew silently — every LLM call then 401s until a human
re-authorises. On 2026-07-25 that went unnoticed for 37 days.

`claude auth login` is interactive: it prints an OAuth URL, then blocks
on stdin waiting for the code the browser hands back. It needs a real
pty — on a plain pipe the prompt never appears. This module drives that
exchange so the user can complete it from Settings instead of needing
shell access to the container.

Deliberately NOT per-user: the credential lives in one machine-wide
volume, so a login is a host-level operation. One session at a time.
"""

from __future__ import annotations

import asyncio
import logging
import os
import pty
import re
import signal
import subprocess
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lazyclaw.gateway.auth import User, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/claude-auth", tags=["claude-auth"])

# A login left half-finished holds a `claude` process open. Reap it well
# before the OAuth code itself would expire.
_SESSION_TTL_S = 600.0
# The CLI prints the URL within a second or two; anything longer means it
# errored or changed its output shape.
_URL_WAIT_S = 25.0
_SUBMIT_WAIT_S = 60.0

_URL_RE = re.compile(r"https://\S*/oauth/authorize\?\S+")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][B0]|[\x00-\x08\x0b-\x1f\x7f]")
# The browser hands back an opaque code, sometimes suffixed "#<state>".
# Anything outside this charset is not a code — reject before it reaches
# the pty rather than feeding arbitrary bytes to a subprocess stdin.
_CODE_RE = re.compile(r"^[A-Za-z0-9._~#\-]{8,512}$")


def _clean(raw: bytes) -> str:
    """Strip ANSI/cursor noise so the URL and prompts are greppable.

    `claude auth login` is plain enough, but the sibling `setup-token`
    flow renders a full TUI — keep this tolerant of both.
    """
    return _ANSI_RE.sub("", raw.decode("utf-8", "replace"))


class _LoginSession:
    """One `claude auth login` process attached to a pty."""

    def __init__(self) -> None:
        self.id = uuid.uuid4().hex
        self.started_at = time.monotonic()
        self.buffer = ""
        self.url: str | None = None
        self._master, slave = pty.openpty()
        # A narrow terminal hard-wraps the URL across several lines and
        # breaks any attempt to read it back out. Ask for a very wide one.
        try:
            import fcntl
            import struct
            import termios

            fcntl.ioctl(
                self._master, termios.TIOCSWINSZ,
                struct.pack("HHHH", 50, 1000, 0, 0),
            )
        except Exception:  # pragma: no cover - platform dependent
            logger.debug("could not widen pty; URL may wrap", exc_info=True)

        env = dict(os.environ)
        # A set API key outranks the claude.ai login and makes the CLI
        # skip the subscription flow entirely.
        env["ANTHROPIC_API_KEY"] = ""
        env["ANTHROPIC_AUTH_TOKEN"] = ""
        env["COLUMNS"] = "1000"

        self.proc = subprocess.Popen(
            ["claude", "auth", "login"],
            stdin=slave, stdout=slave, stderr=slave,
            env=env, close_fds=True, start_new_session=True,
        )
        os.close(slave)

    def drain(self) -> str:
        """Non-blocking read of whatever the CLI has emitted so far."""
        import select

        while True:
            r, _, _ = select.select([self._master], [], [], 0)
            if not r:
                break
            try:
                chunk = os.read(self._master, 4096)
            except OSError:
                break
            if not chunk:
                break
            self.buffer += _clean(chunk)
        if self.url is None:
            # The URL can arrive split across reads; match on the whole
            # buffer with whitespace removed so a wrap can't hide it.
            match = _URL_RE.search(self.buffer.replace("\n", "").replace(" ", ""))
            if match:
                self.url = match.group(0)
        return self.buffer

    def write_code(self, code: str) -> None:
        """Answer the 'Paste code here' prompt. The \\r is required —
        a bare newline leaves the CLI waiting forever."""
        os.write(self._master, (code + "\r").encode())

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.started_at) > _SESSION_TTL_S

    def close(self) -> None:
        """Kill the process group and release the pty. Safe to call twice."""
        if self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except Exception:
                self.proc.kill()
        try:
            os.close(self._master)
        except OSError:
            pass


# Single-flight: one machine-wide credential means one login at a time.
_active: _LoginSession | None = None


def _reap_if_stale() -> None:
    global _active
    if _active is not None and _active.expired:
        logger.info("[claude-auth] reaping expired login session")
        _active.close()
        _active = None


class SubmitBody(BaseModel):
    session_id: str
    code: str


@router.get("/status")
async def status(user: User = Depends(get_current_user)):
    """Token health — expiry-aware, not just 'is the file there'."""
    from lazyclaw.llm.providers.claude_sdk_provider import (
        _classify_oauth_credentials,
    )

    import json

    path = os.path.expanduser("~/.claude/.credentials.json")
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return {"logged_in": False, "detail": "No credential file — never logged in."}

    try:
        with open(path, encoding="utf-8") as fh:
            cred = json.load(fh)
    except (OSError, ValueError) as exc:
        return {"logged_in": False, "detail": f"Credential unreadable: {exc}"}

    ok, problem = _classify_oauth_credentials(cred, int(time.time() * 1000))
    oauth = cred.get("claudeAiOauth") if isinstance(cred, dict) else None
    oauth = oauth if isinstance(oauth, dict) else (cred if isinstance(cred, dict) else {})
    expires_at = oauth.get("expiresAt")

    return {
        "logged_in": ok,
        "detail": problem or "Token valid.",
        "expires_at": expires_at,
        "subscription": oauth.get("subscriptionType"),
    }


@router.post("/start")
async def start(user: User = Depends(get_current_user)):
    """Spawn the login and hand back the OAuth URL to visit."""
    global _active
    _reap_if_stale()
    if _active is not None:
        raise HTTPException(
            409,
            "A login is already in progress. Finish it or cancel it first.",
        )

    try:
        session = _LoginSession()
    except Exception as exc:
        logger.error("[claude-auth] could not start login: %s", exc)
        raise HTTPException(500, f"Could not start `claude auth login`: {exc}")

    deadline = time.monotonic() + _URL_WAIT_S
    while time.monotonic() < deadline:
        session.drain()
        if session.url:
            _active = session
            logger.info("[claude-auth] login started, awaiting code")
            return {"session_id": session.id, "url": session.url}
        if session.proc.poll() is not None:
            break
        await asyncio.sleep(0.25)

    tail = session.buffer[-400:]
    session.close()
    logger.error("[claude-auth] no OAuth URL from CLI. tail=%r", tail)
    raise HTTPException(
        504,
        "Timed out waiting for the sign-in URL. The CLI said: "
        + (tail.strip() or "(nothing)"),
    )


@router.post("/submit")
async def submit(body: SubmitBody, user: User = Depends(get_current_user)):
    """Feed the pasted code to the waiting prompt and report the result."""
    global _active
    _reap_if_stale()
    if _active is None or _active.id != body.session_id:
        raise HTTPException(410, "Login session expired. Start again.")

    code = body.code.strip()
    if not _CODE_RE.match(code):
        raise HTTPException(400, "That doesn't look like a valid code.")

    session = _active
    try:
        session.write_code(code)
    except OSError as exc:
        session.close()
        _active = None
        raise HTTPException(500, f"Could not send the code: {exc}")

    deadline = time.monotonic() + _SUBMIT_WAIT_S
    while time.monotonic() < deadline:
        session.drain()
        if session.proc.poll() is not None:
            break
        await asyncio.sleep(0.25)

    session.drain()
    tail = session.buffer[-600:]
    rc = session.proc.poll()
    session.close()
    _active = None

    # The CLI's exit code is not trustworthy on its own here, so confirm
    # against the credential the login was supposed to write.
    from lazyclaw.llm.providers.claude_sdk_provider import check_claude_sdk_auth

    ok, problem = check_claude_sdk_auth()
    if ok:
        logger.info("[claude-auth] re-authenticated successfully")
        return {"ok": True, "detail": "Signed in. Claude is ready."}

    logger.error("[claude-auth] login failed rc=%s problem=%s", rc, problem)
    raise HTTPException(
        400,
        f"Sign-in did not complete: {problem or 'unknown'}. "
        f"CLI output: {tail.strip()[-300:] or '(nothing)'}",
    )


@router.post("/cancel")
async def cancel(user: User = Depends(get_current_user)):
    """Abandon an in-flight login and free the slot."""
    global _active
    if _active is not None:
        _active.close()
        _active = None
    return {"ok": True}
