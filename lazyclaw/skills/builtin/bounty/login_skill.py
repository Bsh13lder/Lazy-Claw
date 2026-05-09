"""bounty_login — open the program's login page in the user's browser,
pause for them to solve any CAPTCHA / 2FA in person, then capture session
cookies for every in-scope host. Cookies are encrypted at rest and reused
by `bounty_probe` so authenticated probes are possible without leaking the
researcher's account password to the agent.

Why this works under program rules: the user is performing the actual
authentication. The agent only handles the orchestration and cookie
handoff. Allegro / Intigriti rules say "only interact with sandbox
accounts you own" — registering and authenticating IS the way to own one.
The CAPTCHA is solved by a human in their own browser, no bypass.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from lazyclaw.browser.checkpoints import request_checkpoint
from lazyclaw.skills.base import BaseSkill
from lazyclaw.skills.builtin.bounty import store

logger = logging.getLogger(__name__)


def _scope_to_cookie_url(asset: str) -> str:
    """Convert a scope pattern to a URL we can hand to Network.getCookies.

    Network.getCookies takes URLs and returns cookies whose domain/path
    matches. We strip wildcards and emit the bare host as https://host/.
    """
    asset = asset.strip()
    if asset.startswith("*."):
        asset = asset[2:]
    if "://" not in asset:
        asset = f"https://{asset}"
    return asset


class BountyLoginSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "bounty_login"

    @property
    def display_name(self) -> str:
        return "Bounty: capture session cookies"

    @property
    def category(self) -> str:
        return "bounty"

    @property
    def description(self) -> str:
        return (
            "Open the program's login URL in the user's browser, pause "
            "via a checkpoint until the user has signed in (solving any "
            "CAPTCHA or 2FA themselves), then capture session cookies for "
            "every in-scope host. Cookies are encrypted and reused by "
            "bounty_probe / bounty_hunt for authenticated requests. "
            "Required when probing endpoints that need a logged-in session "
            "(most modern web/edge APIs)."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "program_name": {
                    "type": "string",
                    "description": "Name of a registered bounty program.",
                },
                "login_url": {
                    "type": "string",
                    "description": (
                        "URL to navigate to so the user can sign in. Usually "
                        "the program's sandbox login page (e.g. "
                        "https://allegro.pl.allegrosandbox.pl/logowanie). "
                        "May land outside the wildcard scope on apex login "
                        "domains — that's expected; the cookies are what "
                        "matter and we never test the apex itself."
                    ),
                },
                "approval_timeout_seconds": {
                    "type": "integer",
                    "minimum": 60,
                    "maximum": 1800,
                    "description": (
                        "How long to wait for the user to finish login "
                        "(default 600 = 10 min)."
                    ),
                },
            },
            "required": ["program_name", "login_url"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        program_name = params["program_name"]
        login_url = params["login_url"]
        timeout = int(params.get("approval_timeout_seconds") or 600)

        program = await store.get_program(self._config, user_id, program_name)
        if not program:
            return f"❌ No program '{program_name}'. Register first with bounty_register_program."
        if not program["enabled"]:
            return f"❌ Program '{program_name}' is disabled."

        # Lazy import — avoid pulling browser stack into modules that don't
        # use it (keeps test runs fast).
        try:
            from lazyclaw.skills.builtin.browser_actions.backends import (
                get_cdp_backend,
            )
        except ImportError as exc:
            return f"❌ Browser backend not available: {exc}"

        backend = await get_cdp_backend(user_id)
        if backend is None:
            return (
                "❌ No browser runtime — start Brave/Chrome with CDP "
                "(check `lazyclaw setup` or `make host-bridge`)."
            )

        # 1. Navigate to the login page in the user's primary tab. We use
        # the same tab the user is already in so the login flow lands on a
        # session they can see.
        try:
            await backend.open(login_url)
        except Exception as exc:
            return f"❌ Could not open {login_url}: {exc}"

        await store.write_audit(
            self._config, user_id, program_id=program["id"],
            target=login_url, tool="login", method="navigate",
            decision="allow",
        )

        # 2. Block until the user clicks Approve. Same UX as
        # request_user_approval — they see a checkpoint card on the canvas
        # / Telegram with a description of what to confirm.
        decision = await request_checkpoint(
            user_id,
            f"bounty_login_{program_name}",
            detail=(
                f"Sign in to {program_name} in the open browser tab "
                f"({login_url}). Solve any CAPTCHA / 2FA yourself. "
                "Click Approve once you're logged in — I'll capture "
                "the session cookies."
            ),
            timeout=timeout,
        )
        if not decision.get("approved"):
            reason = decision.get("reason") or "rejected"
            return f"❌ Login checkpoint not approved: {reason}"

        # 3. Collect cookies for every scope asset via Network.getCookies.
        # We pass URLs (one per scope domain) so CDP returns the matching
        # cookies for each — even if the cookie Domain attr is broader
        # (e.g. .allegro.pl.allegrosandbox.pl) it'll surface here.
        cookie_urls = sorted({_scope_to_cookie_url(a) for a in program["scope_assets"]})
        # Also ask for cookies on the login URL itself (apex), since that's
        # where the session cookie was actually set.
        login_host = urlparse(login_url).netloc
        if login_host:
            cookie_urls.append(f"https://{login_host}/")

        try:
            conn = await backend._ensure_connected()  # type: ignore[attr-defined]
            result = await conn.send(
                "Network.getCookies", {"urls": cookie_urls}
            )
            cookies_raw = result.get("cookies", []) if isinstance(result, dict) else []
        except Exception as exc:
            return f"❌ CDP Network.getCookies failed: {exc}"

        if not cookies_raw:
            return (
                "⚠️ Login approved but no cookies captured for the scope "
                "domains. You may not actually be signed in, or the "
                "session cookie domain doesn't overlap the scope."
            )

        # 4. Filter to authentication-shaped cookies. Heuristic: keep
        # everything that's HttpOnly OR matches a known session-cookie
        # name pattern. Other tracking cookies are noise.
        SESSION_HINTS = (
            "session", "sess", "auth", "token", "jwt",
            "csrf", "xsrf", "id", "user", "login",
        )
        kept = []
        for c in cookies_raw:
            name_lc = (c.get("name") or "").lower()
            is_httponly = bool(c.get("httpOnly"))
            looks_session = any(h in name_lc for h in SESSION_HINTS)
            if is_httponly or looks_session:
                kept.append({
                    "name": c.get("name"),
                    "value": c.get("value"),
                    "domain": c.get("domain"),
                    "path": c.get("path", "/"),
                    "secure": bool(c.get("secure", True)),
                    "httpOnly": is_httponly,
                    "expires": c.get("expires", -1),
                    "sameSite": c.get("sameSite"),
                })

        if not kept:
            # Fallback: keep everything if we couldn't identify session cookies.
            kept = [
                {
                    "name": c.get("name"),
                    "value": c.get("value"),
                    "domain": c.get("domain"),
                    "path": c.get("path", "/"),
                    "secure": bool(c.get("secure", True)),
                    "httpOnly": bool(c.get("httpOnly")),
                    "expires": c.get("expires", -1),
                    "sameSite": c.get("sameSite"),
                }
                for c in cookies_raw
            ]

        # 5. Encrypt + store. Names only are echoed to the user so they
        # know what was captured without leaking values.
        await store.save_session_cookies(self._config, user_id, program_name, kept)

        names = sorted({c["name"] for c in kept if c.get("name")})
        cookie_summary = ", ".join(names[:8])
        if len(names) > 8:
            cookie_summary += f" … (+{len(names) - 8} more)"

        await store.write_audit(
            self._config, user_id, program_id=program["id"],
            target=login_url, tool="login", method="cookie_capture",
            decision="allow", response_code=len(kept),
        )

        return (
            f"✅ Captured {len(kept)} cookies for **{program_name}** "
            f"({len(names)} unique names).\n"
            f"Names: {cookie_summary}\n"
            f"Encrypted at rest (AAD-bound to user). Use bounty_probe / "
            f"bounty_hunt next for authenticated requests."
        )
