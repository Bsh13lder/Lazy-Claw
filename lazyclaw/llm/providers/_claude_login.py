"""One-click Claude-subscription login — pty-driven ``claude setup-token``.

``claude setup-token`` runs the OAuth device flow but REQUIRES a real TTY (with
no terminal it just loops a "config not found" warning and never emits a URL).
So we drive it inside a pseudo-terminal:

  start()    → spawn ``claude setup-token`` in a pty, capture the
               ``https://claude.com/…/oauth/authorize`` URL it prints, keep the
               child alive keyed by a random ``login_id``, return the URL.
  complete() → write the short code (that Anthropic shows after the user
               authorises) into the pty, capture the minted ``sk-ant-oat…``
               token, persist it via ``_claude_token.write_claude_oauth_token``.

The live pty + child are held in-process between the two HTTP calls (single
gateway worker). Sessions self-expire so an abandoned login can't leak a
process. All pty I/O is blocking → callers MUST invoke via ``asyncio.to_thread``
so the event loop never stalls.
"""

from __future__ import annotations

import logging
import os
import pty
import re
import secrets
import select
import subprocess
import time

from lazyclaw.llm.providers._claude_token import write_claude_oauth_token

logger = logging.getLogger(__name__)

_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
_URL_MARKER = "https://claude.com"
_URL_CHARS = r"A-Za-z0-9%_=&?:/.\-"
_TOKEN_MARKER = "sk-ant-oat"
_TOKEN_CHARS = r"A-Za-z0-9_\-"

_SESSION_TTL = 300.0   # abandoned-login reap window (5 min)
_URL_TIMEOUT = 30.0    # wait for the authorize URL to appear
# A VALID code triggers a real network token-exchange (mint + write creds +
# print token) that can take well over 25s from inside the container; only an
# INVALID code rejects fast ("OAuth error"). Give the exchange generous room —
# an invalid code still short-circuits early via the OAuth-error check.
_TOKEN_TIMEOUT = 90.0  # wait for the token after the code is submitted

# login_id → {"master": fd, "proc": Popen, "created": ts, "buf": bytes}
_sessions: dict[str, dict] = {}


def _strip_ansi(raw: bytes) -> str:
    return _ANSI.sub("", raw.decode(errors="replace"))


def _extract_wrapped(text: str, marker: str, char_class: str) -> str | None:
    """Reconstruct a terminal-wrapped token/URL.

    The CLI hard-wraps long strings at the pty width, so the URL/token spans
    several lines. Join the marker line with each following line that is ONLY
    valid chars, stopping at the first blank or non-matching line (the prompt
    that follows).
    """
    idx = text.find(marker)
    if idx < 0:
        return None
    lines = text[idx:].split("\n")
    out = lines[0].strip()
    cont = re.compile(f"^[{char_class}]+$")
    for line in lines[1:]:
        seg = line.strip()
        if not seg or not cont.match(seg):
            break
        out += seg
    return out or None


def _kill(login_id: str) -> None:
    sess = _sessions.pop(login_id, None)
    if not sess:
        return
    _teardown(sess.get("proc"), sess.get("master"))


def _teardown(proc: "subprocess.Popen | None", master: "int | None") -> None:
    if master is not None:
        try:
            os.close(master)
        except OSError:
            pass
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _reap_expired() -> None:
    now = time.time()
    for lid in [k for k, v in _sessions.items() if now - v["created"] > _SESSION_TTL]:
        logger.info("Reaping expired Claude login session %s", lid)
        _kill(lid)


def start_login() -> dict:
    """Spawn ``claude setup-token`` in a pty and return {login_id, auth_url}."""
    _reap_expired()
    master, slave = pty.openpty()
    # Null ANTHROPIC_* so the flow uses subscription OAuth, never a stray key.
    env = {
        **os.environ,
        "ANTHROPIC_API_KEY": "",
        "ANTHROPIC_AUTH_TOKEN": "",
        "ANTHROPIC_BASE_URL": "",
    }
    proc = subprocess.Popen(
        ["claude", "setup-token"],
        stdin=slave, stdout=slave, stderr=slave, close_fds=True, env=env,
    )
    os.close(slave)

    buf = b""
    deadline = time.time() + _URL_TIMEOUT
    url: str | None = None
    while time.time() < deadline:
        ready, _, _ = select.select([master], [], [], 1)
        if ready:
            try:
                data = os.read(master, 4096)
            except OSError:
                break
            if not data:
                break
            buf += data
            url = _extract_wrapped(_strip_ansi(buf), _URL_MARKER, _URL_CHARS)
            if url:
                break

    if not url:
        _teardown(proc, master)
        raise RuntimeError(
            "Couldn't get the Claude login URL — the CLI may not be installed "
            "or reachable. Try again, or run `claude setup-token` in a terminal."
        )

    login_id = secrets.token_urlsafe(12)
    _sessions[login_id] = {"master": master, "proc": proc, "created": time.time(), "buf": buf}
    logger.info("Claude login session %s started (URL captured)", login_id)
    return {"login_id": login_id, "auth_url": url}


def complete_login(login_id: str, code: str) -> dict:
    """Feed the auth code into the pty, capture + persist the token."""
    sess = _sessions.get(login_id)
    if not sess:
        raise RuntimeError("Login session expired or unknown — click Login again.")
    master = sess["master"]
    proc = sess["proc"]
    code = (code or "").strip()
    if not code:
        raise RuntimeError("No code provided.")

    try:
        # CRITICAL: submit with a carriage return, NOT a newline. The CLI's
        # prompt runs the pty in raw mode and only treats `\r` as Enter — a
        # `\n` types the code but never submits it, so setup-token waits forever
        # (the "stuck" bug). Verified 2026-07-05: `\r` → code processed.
        os.write(master, (code + "\r").encode())
    except OSError as exc:
        _kill(login_id)
        raise RuntimeError("Login process is no longer running — click Login again.") from exc

    buf = sess.get("buf", b"")
    token: str | None = None
    invalid = False
    deadline = time.time() + _TOKEN_TIMEOUT
    while time.time() < deadline:
        ready, _, _ = select.select([master], [], [], 1)
        if ready:
            try:
                data = os.read(master, 4096)
            except OSError:
                break
            if data:
                buf += data
                text = _strip_ansi(buf)
                token = _extract_wrapped(text, _TOKEN_MARKER, _TOKEN_CHARS)
                if token:
                    break
                low = text.lower()
                if "oauth error" in low or "invalid code" in low:
                    invalid = True
                    break
        if proc.poll() is not None:  # child exited — final drain
            try:
                ready2, _, _ = select.select([master], [], [], 0.5)
                if ready2:
                    buf += os.read(master, 8192)
            except OSError:
                pass
            token = _extract_wrapped(_strip_ansi(buf), _TOKEN_MARKER, _TOKEN_CHARS)
            break

    _kill(login_id)

    if invalid:
        raise RuntimeError(
            "Invalid code — copy the FULL code from the Claude page (it's long) "
            "and try again. Click 'Login with Claude' to restart."
        )
    if not token:
        # Log the tail of what setup-token actually printed so the token-capture
        # regex can be corrected if the CLI's output shape differs.
        tail = _strip_ansi(buf)[-1200:]
        logger.warning(
            "Claude login %s: no token captured. exited=%s pty tail: %r",
            login_id, proc.poll(), tail,
        )
        raise RuntimeError(
            "Login didn't return a token — the code may be wrong or expired. "
            "Click Login and try again."
        )
    write_claude_oauth_token(token)
    logger.info(
        "Claude login session %s completed — token stored (len=%d)",
        login_id, len(token),
    )
    return {"token_saved": True}
