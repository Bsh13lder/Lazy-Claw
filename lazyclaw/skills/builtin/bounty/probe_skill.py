"""bounty_probe — single authenticated HTTP request, scope-guarded and
audit-logged. The orchestrator (bounty_hunt) calls this in a loop; the
agent can also call it directly for ad-hoc probes.

Responsibilities:
  1. Verify the URL is in scope via the deterministic ScopeChecker.
     Out-of-scope = refuse, audit, return.
  2. Load session cookies if available; attach the matching ones.
  3. Apply program rate limit (rate_limit_rps) — single per-process
     token bucket keyed by program_id is sufficient since every probe
     for a given program runs serially through this skill.
  4. Tag the User-Agent with the user's Intigriti email (program rules
     require it for Allegro and is good practice elsewhere).
  5. Send the request. Audit every result with status code + decision.
  6. Return a structured summary so the orchestrator can score impact.

Strictly read-shaped: GET / HEAD / OPTIONS only. POST/PUT/DELETE are
gated by the upstream AutopilotGuard pattern — adding them here without
the guard would be a regression. Future task: integrate AutopilotGuard
for safe-method default + ASK gate on unsafe methods.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from lazyclaw.skills.base import BaseSkill
from lazyclaw.skills.builtin.bounty import store

logger = logging.getLogger(__name__)


# Per-program rate-limit state. {program_id: (last_request_ts, min_interval)}
_rate_state: dict[str, tuple[float, float]] = {}


# Methods we'll send without further approval. The upstream AutopilotGuard
# splits HTTP methods into safe vs unsafe; we mirror that here as a
# defense-in-depth layer.
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _build_cookie_header(
    cookies: list[dict[str, Any]], target_host: str, target_path: str = "/",
) -> str:
    """Build a Cookie: header string with cookies that match the target URL.

    Honors Domain (suffix-match incl. leading dot) and Path (prefix-match).
    """
    pairs: list[str] = []
    target_host_lc = target_host.lower()
    for c in cookies:
        domain = (c.get("domain") or "").lower().lstrip(".")
        if not domain:
            continue
        if not (target_host_lc == domain or target_host_lc.endswith("." + domain)):
            continue
        path = c.get("path") or "/"
        if not target_path.startswith(path):
            continue
        # Skip expired cookies
        expires = c.get("expires")
        if isinstance(expires, (int, float)) and 0 < expires < time.time():
            continue
        n, v = c.get("name"), c.get("value")
        if n is None or v is None:
            continue
        pairs.append(f"{n}={v}")
    return "; ".join(pairs)


def _enforce_rate(program_id: str, rate_rps: int) -> None:
    """Block (sync) until the per-program rate limit allows another request."""
    if rate_rps <= 0:
        return
    min_interval = 1.0 / float(rate_rps)
    last, _ = _rate_state.get(program_id, (0.0, min_interval))
    elapsed = time.time() - last
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _rate_state[program_id] = (time.time(), min_interval)


class BountyProbeSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "bounty_probe"

    @property
    def display_name(self) -> str:
        return "Bounty: send a probe"

    @property
    def category(self) -> str:
        return "bounty"

    @property
    def description(self) -> str:
        return (
            "Send a single scope-guarded HTTP probe to a registered bounty "
            "program target. Attaches saved session cookies if present, "
            "respects the program's rate limit, audit-logs every request. "
            "Read-shaped methods only (GET / HEAD / OPTIONS) — unsafe "
            "methods are refused. Returns status, content-type, size, and "
            "first 4KB of body so the caller can detect reflection / "
            "verbose errors / open-redirect Locations."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "program_name": {"type": "string"},
                "url": {
                    "type": "string",
                    "description": "Full URL to probe. Must match an in-scope pattern.",
                },
                "method": {
                    "type": "string",
                    "enum": ["GET", "HEAD", "OPTIONS"],
                    "description": "HTTP method. Default GET.",
                },
                "accept": {
                    "type": "string",
                    "description": "Accept header value (default 'application/json, */*').",
                },
                "extra_headers": {
                    "type": "object",
                    "description": "Optional extra request headers.",
                },
                "intigriti_email": {
                    "type": "string",
                    "description": (
                        "User's Intigriti email/handle. Tagged into the "
                        "User-Agent header per Intigriti researcher rules."
                    ),
                },
                "follow_redirects": {
                    "type": "boolean",
                    "description": "Default false — we want to see Location.",
                },
            },
            "required": ["program_name", "url"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        program_name = params["program_name"]
        url = params["url"]
        method = (params.get("method") or "GET").upper()
        if method not in _SAFE_METHODS:
            return f"❌ Method {method} is not in the safe set; bounty_probe refuses."

        program = await store.get_program(self._config, user_id, program_name)
        if not program:
            return f"❌ No program '{program_name}'."
        if not program["enabled"]:
            return f"❌ Program '{program_name}' is disabled."

        # Scope check
        try:
            from claude_bug_bounty import ScopeChecker
        except ImportError as exc:
            return f"❌ claude_bug_bounty not installed: {exc}"

        checker = ScopeChecker(
            domains=program["scope_assets"],
            excluded_domains=program["excluded_assets"] or None,
            excluded_classes=program["excluded_classes"] or None,
        )
        if not checker.is_in_scope(url):
            await store.write_audit(
                self._config, user_id, program_id=program["id"],
                target=url, tool="probe", method=method, decision="refuse",
            )
            return f"🚫 [scope_refused]: {url}"

        # Cookies
        cookies, _saved = await store.load_session_cookies(
            self._config, user_id, program_name,
        )
        parsed = urlparse(url)
        cookie_header = _build_cookie_header(
            cookies, parsed.hostname or "", parsed.path or "/",
        )

        # UA
        intigriti_email = (params.get("intigriti_email") or "").strip()
        ua = (
            f"Mozilla/5.0 lazyclaw {intigriti_email} (intigriti-bug-bounty)"
            if intigriti_email
            else "Mozilla/5.0 lazyclaw (intigriti-bug-bounty)"
        )

        headers: dict[str, str] = {
            "User-Agent": ua,
            "Accept": params.get("accept") or "application/json, */*",
        }
        if cookie_header:
            headers["Cookie"] = cookie_header
        for k, v in (params.get("extra_headers") or {}).items():
            headers[str(k)] = str(v)

        # Rate limit
        _enforce_rate(program["id"], int(program.get("rate_limit_rps") or 5))

        # Build request — disable redirect following so Location is visible.
        req = urllib.request.Request(url, method=method, headers=headers)
        follow = bool(params.get("follow_redirects", False))

        if follow:
            opener = urllib.request.build_opener()
        else:
            class _NoRedirect(urllib.request.HTTPRedirectHandler):
                def http_error_302(self, req, fp, code, msg, headers):
                    return None
                http_error_301 = http_error_303 = http_error_307 = http_error_302
            opener = urllib.request.build_opener(_NoRedirect)

        t0 = time.time()
        try:
            resp = await asyncio.to_thread(opener.open, req, None, 12)
            status = getattr(resp, "status", 0) or resp.getcode()
            body = await asyncio.to_thread(resp.read, 60000)
            resp_headers = dict(resp.headers)
        except urllib.error.HTTPError as e:
            status = e.code
            body = (e.read(8000) if e.fp else b"")
            resp_headers = dict(e.headers) if e.headers else {}
        except Exception as e:
            status = 0
            body = (f"NETERR: {e!r}").encode("utf-8")
            resp_headers = {}
        elapsed_ms = int((time.time() - t0) * 1000)

        await store.write_audit(
            self._config, user_id, program_id=program["id"],
            target=url, tool="probe", method=method,
            decision="allow", response_code=int(status),
        )

        size = len(body)
        try:
            body_text = body.decode("utf-8", errors="replace")
        except Exception:
            body_text = ""
        body_excerpt = body_text[:4000]

        # Format response
        location = resp_headers.get("Location") or resp_headers.get("location") or ""
        ct = resp_headers.get("Content-Type") or resp_headers.get("content-type") or ""
        srv = resp_headers.get("Server") or resp_headers.get("server") or ""

        summary = {
            "url": url,
            "method": method,
            "status": int(status),
            "size_bytes": size,
            "content_type": ct,
            "server": srv,
            "location": location,
            "elapsed_ms": elapsed_ms,
            "had_cookies": bool(cookie_header),
            "body_excerpt": body_excerpt,
        }
        return json.dumps(summary, ensure_ascii=False, indent=2)
