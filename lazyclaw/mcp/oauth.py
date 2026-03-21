"""OAuth 2.1 with PKCE for remote MCP servers.

Browser-based auth: when a remote MCP server needs OAuth,
LazyClaw opens Brave to handle the login flow automatically.
Tokens stored encrypted in the credential vault.

Supports Dynamic Client Registration (RFC 7591) for servers
that don't recognise a static client_id.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from lazyclaw.mcp.token_store import OAuthTokenData

logger = logging.getLogger(__name__)

_OAUTH_TIMEOUT = 300.0  # 5 minutes — user may need MFA
_DEFAULT_CLIENT_ID = "lazyclaw"


# ── Data models ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OAuthMetadata:
    """Immutable OAuth server discovery metadata."""

    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None = None
    scopes_supported: tuple[str, ...] = ()


# ── PKCE ────────────────────────────────────────────────────────────────


def generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256).

    Returns (code_verifier, code_challenge).
    """
    verifier = secrets.token_urlsafe(64)  # 86 chars, well above 43-char minimum
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ── Metadata discovery ──────────────────────────────────────────────────


async def discover_oauth_metadata(
    resource_metadata_url: str,
) -> OAuthMetadata | None:
    """Fetch OAuth metadata from a resource metadata URL.

    Follows the MCP OAuth flow:
    1. GET resource_metadata_url → find authorization_servers
    2. GET authorization_server/.well-known/oauth-authorization-server
    3. Extract endpoints
    """
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            meta_resp = await client.get(resource_metadata_url)
            if meta_resp.status_code != 200:
                logger.debug(
                    "Resource metadata %s returned %d",
                    resource_metadata_url, meta_resp.status_code,
                )
                return None

            data = meta_resp.json()
            auth_servers = data.get("authorization_servers", [])

            if auth_servers:
                as_url = (
                    auth_servers[0].rstrip("/")
                    + "/.well-known/oauth-authorization-server"
                )
                as_resp = await client.get(as_url)
                if as_resp.status_code == 200:
                    as_data = as_resp.json()
                    return OAuthMetadata(
                        authorization_endpoint=as_data["authorization_endpoint"],
                        token_endpoint=as_data["token_endpoint"],
                        registration_endpoint=as_data.get("registration_endpoint"),
                        scopes_supported=tuple(
                            as_data.get("scopes_supported", [])
                        ),
                    )

            # Fallback: .well-known at resource origin
            parsed = urlparse(resource_metadata_url)
            well_known = (
                f"{parsed.scheme}://{parsed.netloc}"
                "/.well-known/oauth-authorization-server"
            )
            resp = await client.get(well_known)
            if resp.status_code == 200:
                wk_data = resp.json()
                return OAuthMetadata(
                    authorization_endpoint=wk_data["authorization_endpoint"],
                    token_endpoint=wk_data["token_endpoint"],
                    registration_endpoint=wk_data.get("registration_endpoint"),
                    scopes_supported=tuple(
                        wk_data.get("scopes_supported", [])
                    ),
                )
        except Exception as exc:
            logger.debug("OAuth metadata discovery failed: %s", exc)

    return None


# ── Dynamic Client Registration (RFC 7591) ──────────────────────────────


async def register_client(
    registration_endpoint: str,
    redirect_uri: str,
    client_name: str = "LazyClaw",
) -> tuple[str, str | None]:
    """Register as a dynamic OAuth client.

    Returns (client_id, client_secret or None).
    Falls back to static default on failure.
    """
    payload = {
        "client_name": client_name,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",  # public client, PKCE only
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                registration_endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            client_id = data["client_id"]
            client_secret = data.get("client_secret")
            logger.info(
                "Dynamic client registered: client_id=%s", client_id,
            )
            return client_id, client_secret
    except Exception as exc:
        logger.warning(
            "Dynamic client registration failed (%s) — using static '%s'",
            exc, _DEFAULT_CLIENT_ID,
        )
        return _DEFAULT_CLIENT_ID, None


# ── Browser helper ──────────────────────────────────────────────────────


async def _open_browser_tab(config, user_id: str, url: str) -> tuple:
    """Open a visible Brave tab for OAuth login.

    Returns (cdp_backend, target_id) for cleanup.
    Inlines the _get_visible_cdp_backend pattern to avoid circular imports.
    """
    from lazyclaw.browser.cdp import find_chrome_cdp
    from lazyclaw.browser.cdp_backend import CDPBackend

    port = getattr(config, "cdp_port", 9222)
    profile_dir = str(config.database_dir / "browser_profiles" / user_id)

    ws_url = await find_chrome_cdp(port)
    if not ws_url:
        # Launch visible Brave
        chrome_bin = getattr(config, "browser_executable", None) or "google-chrome"
        os.makedirs(profile_dir, exist_ok=True)
        await asyncio.create_subprocess_exec(
            chrome_bin,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--disable-blink-features=AutomationControlled",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        logger.info("Launched visible Brave for OAuth (port=%d)", port)
        for _ in range(20):
            await asyncio.sleep(0.5)
            if await find_chrome_cdp(port):
                break

    backend = CDPBackend(port=port, profile_dir=profile_dir)
    target_id = await backend.new_tab(url)
    logger.info("OAuth: opened browser tab to %s", urlparse(url).netloc)
    return backend, target_id


# ── Main OAuth flow ─────────────────────────────────────────────────────


async def run_oauth_flow(
    config,
    user_id: str,
    server_name: str,
    server_url: str,
    resource_metadata_url: str,
) -> OAuthTokenData:
    """Complete OAuth 2.1 PKCE flow using the user's Brave browser.

    1. Discover OAuth endpoints from resource metadata
    2. Dynamic client registration if supported (RFC 7591)
    3. Start local callback server
    4. Open Brave to authorization URL
    5. User logs in / approves
    6. Catch callback with authorization code
    7. Exchange for tokens
    8. Store encrypted in vault
    """
    from lazyclaw.mcp.token_store import save_tokens

    # 1. Discover metadata
    metadata = await discover_oauth_metadata(resource_metadata_url)
    if not metadata:
        raise RuntimeError(
            f"Could not discover OAuth metadata from {resource_metadata_url}"
        )

    # 2. PKCE
    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(32)

    # 3. Start callback server — bind first to get port, then await code
    code_future: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async def _handle_cb(reader, writer):
        try:
            raw = await reader.read(4096)
            line = raw.decode("utf-8", errors="replace").split("\r\n")[0]
            path = line.split(" ")[1] if " " in line else ""
            params = parse_qs(urlparse(path).query)

            code = params.get("code", [""])[0]
            cb_state = params.get("state", [""])[0]
            error = params.get("error", [""])[0]
            error_desc = params.get("error_description", [""])[0]

            body = (
                "<html><body style='font-family:sans-serif;text-align:center;"
                "padding:60px;'>"
                + (
                    f"<h1>Authorization Failed</h1><p>{error}: {error_desc}</p>"
                    if error
                    else "<h1>Connected!</h1>"
                    "<p>You can close this tab and return to LazyClaw.</p>"
                )
                + "</body></html>"
            )
            header = (
                f"HTTP/1.1 200 OK\r\n"
                f"Content-Type: text/html\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n\r\n"
            )
            writer.write((header + body).encode())
            await writer.drain()
            writer.close()

            if code_future.done():
                return
            if error:
                code_future.set_exception(
                    RuntimeError(f"OAuth error: {error} — {error_desc}")
                )
            elif cb_state != state:
                code_future.set_exception(
                    RuntimeError("OAuth state mismatch — possible CSRF")
                )
            elif code:
                code_future.set_result(code)
            else:
                code_future.set_exception(
                    RuntimeError("No authorization code in callback")
                )
        except Exception as exc:
            if not code_future.done():
                code_future.set_exception(exc)

    cb_server = await asyncio.start_server(_handle_cb, "127.0.0.1", 0)
    callback_port = cb_server.sockets[0].getsockname()[1]
    redirect_uri = f"http://127.0.0.1:{callback_port}/callback"

    # 4. Dynamic client registration (if supported)
    client_id = _DEFAULT_CLIENT_ID
    if metadata.registration_endpoint:
        client_id, _ = await register_client(
            metadata.registration_endpoint, redirect_uri,
        )

    # 5. Build authorization URL
    auth_params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    if metadata.scopes_supported:
        auth_params["scope"] = " ".join(metadata.scopes_supported)

    auth_url = (
        f"{metadata.authorization_endpoint}?{urlencode(auth_params)}"
    )

    # 6. Open browser
    backend = None
    target_id = None
    try:
        async with cb_server:
            backend, target_id = await _open_browser_tab(
                config, user_id, auth_url,
            )
            logger.info(
                "OAuth: waiting for user to complete login (timeout=%ds)",
                int(_OAUTH_TIMEOUT),
            )

            # 7. Wait for callback
            auth_code = await asyncio.wait_for(
                code_future, timeout=_OAUTH_TIMEOUT,
            )
    except asyncio.TimeoutError:
        raise TimeoutError(
            f"OAuth timed out after {int(_OAUTH_TIMEOUT)}s. "
            "The browser tab is still open — complete the login and retry."
        )
    finally:
        # Close the OAuth tab (but not the browser)
        if backend and target_id:
            try:
                await backend.close_tab(target_id)
            except Exception:
                pass

    # 8. Exchange code for tokens
    token_data = await _exchange_code(
        metadata.token_endpoint, auth_code, verifier, redirect_uri, client_id,
    )

    # 9. Build and save token data
    tokens = OAuthTokenData(
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token"),
        expires_at=time.time() + token_data.get("expires_in", 3600),
        scope=token_data.get("scope", ""),
        metadata_url=resource_metadata_url,
        token_endpoint=metadata.token_endpoint,
        client_id=client_id,
    )
    await save_tokens(config, user_id, server_name, tokens)
    logger.info("OAuth complete for %s — tokens stored", server_name)
    return tokens


# ── Token exchange helpers ──────────────────────────────────────────────


async def _exchange_code(
    token_endpoint: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_id: str,
) -> dict:
    """Exchange authorization code + PKCE verifier for tokens."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        return resp.json()


async def refresh_access_token(
    token_endpoint: str,
    refresh_token: str,
    client_id: str = _DEFAULT_CLIENT_ID,
) -> dict:
    """Refresh an expired OAuth token without browser interaction."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            token_endpoint,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        return resp.json()


def parse_resource_metadata_url(www_authenticate: str) -> str | None:
    """Extract resource_metadata URL from WWW-Authenticate header.

    Format: Bearer resource_metadata="https://..."
    """
    match = re.search(r'resource_metadata="([^"]+)"', www_authenticate)
    return match.group(1) if match else None
