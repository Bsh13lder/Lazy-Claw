"""Host browser CDP bridge.

Lets the Docker-hosted agent drive the user's REAL Brave/Chrome browser on
the host machine — with their cookies, saved logins, and extensions — via
CDP over ``host.docker.internal:9222``. VNC takeover (``share_browser_control``)
remains the privacy-preserving alternative for users who don't want to hand
live session access to the agent.

Security model
--------------
CDP exposes arbitrary-file-read and remote-code-execution-class primitives.
To reach the host Brave from the container, Brave has to bind the debugging
port to something the container can reach via ``host.docker.internal`` —
i.e. not just ``127.0.0.1``. That's a real risk on untrusted networks.

Mitigation: we use Chromium's ``--remote-allow-origins=http://lazyclaw-<token>``
flag. The container sends the matching ``Origin`` header on the WS handshake;
clients that reach the port without the token are rejected by Chromium at
the protocol layer. The token is generated per user, stored encrypted in
``users.settings``, and only ever travels inside the Docker network.

This is not bulletproof (a network-capable attacker can still enumerate the
``/json/version`` HTTP endpoint), but it blocks the most common LAN-JS
drive-by. Users get a clear warning on first setup.

The probe / launch-command utilities live here so the skill, HTTP route,
Telegram handler, and CDP backend all use the same seam.
"""

from __future__ import annotations

import logging
import os
import secrets
import sys

import httpx

logger = logging.getLogger(__name__)

# Docker Desktop (Mac/Windows) sets this automatically. On bare Linux Docker
# ``docker-compose.yml`` maps it via ``extra_hosts: ["host.docker.internal:host-gateway"]``.
HOST_GATEWAY_HOSTNAME = "host.docker.internal"

# Default CDP debugging port. Kept in sync with ``cdp.DEFAULT_CDP_PORT``.
DEFAULT_CDP_PORT = 9222

# Token length tuned for readability in the shell one-liner without being
# short enough to brute-force. ``secrets.token_urlsafe(16)`` → ~22 chars.
_TOKEN_BYTES = 16


def is_docker_runtime() -> bool:
    """Best-effort check for "running inside a container on Linux"."""
    if os.getenv("LAZYCLAW_SERVER_MODE", "").lower() in ("true", "1", "yes"):
        return True
    # Fallback — presence of /.dockerenv is a common indicator on Linux.
    return sys.platform == "linux" and os.path.exists("/.dockerenv")


def generate_host_token() -> str:
    """Fresh token for the --remote-allow-origins handshake."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def shared_host_token() -> str | None:
    """Read the shared host-bridge token from the env (set by the launchd
    installer at scripts/install-host-brave-bridge.sh).

    When this is set, EVERY user's host-bridge handshake uses this single
    token — same one embedded in the launchd plist's
    ``--remote-allow-origins=http://lazyclaw-<token>``. Per-user DB tokens
    are still honored for legacy multi-user setups; the env var simply
    takes precedence so a fresh `install-host-brave-bridge.sh` run gives
    you a working bridge with zero per-user setup.
    """
    val = os.getenv("LAZYCLAW_HOST_CDP_TOKEN", "").strip()
    return val or None


def origin_for_token(token: str) -> str:
    """Build the Origin header value the container sends on the WS handshake."""
    return f"http://lazyclaw-{token}"


async def probe_host_cdp(port: int = DEFAULT_CDP_PORT, timeout_s: float = 2.0) -> str | None:
    """Check whether ``host.docker.internal:{port}`` has a CDP endpoint.

    Returns the browser-level WebSocket URL on success, ``None`` otherwise.
    We only probe the unauthenticated HTTP ``/json/version`` endpoint here —
    the Origin-token handshake happens later when ``CDPConnection.connect``
    opens the WebSocket.

    KEY DETAIL: Chromium's CDP HTTP server validates the ``Host:`` header
    and only accepts an IP address or literal ``localhost`` — it rejects
    ``Host: host.docker.internal:9222`` with a 500 ("Host header is
    specified and is not an IP address or localhost"). So we DNS-resolve
    the hostname first and connect to the IP. mcp-upwork's browser/client
    does the same trick — duplicated here intentionally because this probe
    runs from inside the lazyclaw daemon, not the MCP subprocess.
    """
    import socket

    try:
        host_ip = socket.gethostbyname(HOST_GATEWAY_HOSTNAME)
    except OSError as exc:
        logger.debug("Host CDP DNS-resolve failed (%s): %s",
                     HOST_GATEWAY_HOSTNAME, exc)
        return None

    url = f"http://{host_ip}:{port}/json/version"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.debug("Host CDP non-200 (%s): %d", url, resp.status_code)
                return None
            data = resp.json()
            ws_url = data.get("webSocketDebuggerUrl")
            if ws_url:
                logger.info("Host Brave reachable via %s (resolved to %s)",
                            HOST_GATEWAY_HOSTNAME, host_ip)
                return ws_url
    except (httpx.ConnectError, httpx.TimeoutException, Exception) as exc:
        logger.debug("Host CDP probe failed (%s): %s", url, exc)
    return None


async def find_cdp_with_preference(
    port: int,
    *,
    prefer_host: bool,
    token: str | None = None,
) -> tuple[str | None, str]:
    """Locate a CDP endpoint, preferring the host browser when requested.

    Returns ``(ws_url, source)`` where ``source`` is one of:
      - ``"host"``  — connected through ``host.docker.internal``
      - ``"local"`` — connected through ``localhost`` (existing behaviour)
      - ``"none"``  — nothing reachable on either; caller should auto-launch
                      the container Brave as the fallback.

    ``token`` is currently informational — the actual Origin handshake is
    performed by ``CDPConnection.connect``. It's accepted here so future
    probe variants (e.g. lightweight Target.getTargets with the header) can
    plug in without rippling to callers.
    """
    # Import here to avoid a circular import (cdp_backend imports this module).
    from lazyclaw.browser.cdp import find_chrome_cdp

    if prefer_host and is_docker_runtime():
        ws = await probe_host_cdp(port)
        if ws:
            return ws, "host"

    ws = await find_chrome_cdp(port)
    if ws:
        return ws, "local"
    return None, "none"


def build_launch_command(token: str, browser: str = "brave") -> str:
    """Return the shell one-liner the user must run on their host.

    Starts with a "quit first" reminder because Chromium silently ignores
    ``--remote-debugging-port`` when the profile is already open in another
    Brave instance (SingletonLock behaviour).
    """
    origin = origin_for_token(token)

    if browser.lower() == "chrome":
        app_name = "Google Chrome"
        profile_dir = "$HOME/Library/Application Support/Google/Chrome"
    else:
        app_name = "Brave Browser"
        profile_dir = "$HOME/Library/Application Support/BraveSoftware/Brave-Browser"

    # NB: --remote-debugging-address=0.0.0.0 is required so the container can
    # reach the port via host.docker.internal. --remote-allow-origins scopes
    # WS access to the token-bearing origin.
    return (
        "# 1. Quit Brave completely first (Cmd+Q, not just close the window)\n"
        f'osascript -e \'quit app "{app_name}"\' 2>/dev/null; sleep 1\n'
        "# 2. Relaunch with remote debugging enabled (your tabs/logins stay)\n"
        f'open -na "{app_name}" --args \\\n'
        "  --remote-debugging-port=9222 \\\n"
        "  --remote-debugging-address=0.0.0.0 \\\n"
        f"  --remote-allow-origins={origin} \\\n"
        f'  --user-data-dir="{profile_dir}"'
    )


def security_warning() -> str:
    """Short warning message surfaced to the user on first-time setup.

    Kept short so it fits in a Telegram reply, chat bubble, or modal blurb.
    """
    return (
        "Heads up: this makes Brave accept remote control requests on port 9222. "
        "LazyClaw locks it to a secret origin token so random browser tabs can't "
        "reach it, but a laptop on the same Wi-Fi can still probe the port. "
        "Only run this on trusted networks (home/office), and stop the session "
        "with 'stop host browser' when you're done."
    )
