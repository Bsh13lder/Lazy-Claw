"""bounty_hunt — autonomous-ish hunting loop on a registered program.

Strategy (single pass, deterministic recipe — no LLM in the loop yet):
  1. Pull live in-scope hosts from CT logs (same as bounty_recon Phase 1).
  2. For each live host, run a small recipe of probes:
       - root           GET  /
       - robots         GET  /robots.txt
       - sitemap        GET  /sitemap.xml
       - openapi        GET  /openapi.json + /api-docs + /swagger.json
       - graphql        GET  /graphql + /api/graphql
       - debug          GET  /actuator + /actuator/env + /metrics + /debug
       - source-map     GET  /static/scripts/main.js.map (best-effort)
       - dotgit         GET  /.git/config + /.env (high-impact if exposed)
       - reflection     GET  /?_lzm=BOUNTY_MARKER_<id>
                        and GET /?redirect=https://example.org/lzm-bounty
       - method-enum    OPTIONS /
  3. Score each response with the local heuristics:
       - 200 + sourcesContent → source-map exposed
       - 200 + .git config / .env → dotfile exposed (HIGH)
       - 200 + openapi/swagger + paths → spec leak (LOW)
       - 200 + body contains marker → reflection (potential XSS lead)
       - 3xx + Location host == reflection-input host → open redirect (MEDIUM)
       - 5xx + body contains stack trace classes → verbose error (LOW)
  4. Save matched signals as proposed findings via store.create_finding.
  5. Return a summary report.

Worker LLM scoring is deliberately deferred to a later pass — the
deterministic heuristics above are cheap, predictable, and audit-friendly.
The agent can still call bounty_validate_finding to run the 7-Question
Gate on each saved finding before submission.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from lazyclaw.skills.base import BaseSkill
from lazyclaw.skills.builtin.bounty import store

logger = logging.getLogger(__name__)


# Probe recipe — list of (path, name, accept) tuples. Run for each live host.
_RECIPE = [
    ("/",                    "root",        "text/html, */*"),
    ("/robots.txt",          "robots",      "text/plain, */*"),
    ("/sitemap.xml",         "sitemap",     "application/xml, */*"),
    ("/openapi.json",        "openapi",     "application/json"),
    ("/api-docs",            "api_docs",    "application/json"),
    ("/swagger.json",        "swagger",     "application/json"),
    ("/swagger-ui.html",     "swagger_ui",  "text/html"),
    ("/graphql",             "graphql_ep",  "application/json"),
    ("/api/graphql",         "graphql_api", "application/json"),
    ("/actuator",            "actuator",    "application/json"),
    ("/actuator/env",        "actuator_env","application/json"),
    ("/actuator/health",     "actuator_h",  "application/json"),
    ("/metrics",             "metrics",     "text/plain, */*"),
    ("/debug",               "debug",       "application/json, */*"),
    ("/.git/config",         "dotgit",      "text/plain, */*"),
    ("/.env",                "dotenv",      "text/plain, */*"),
    ("/server-status",       "apache_st",   "text/plain, */*"),
]

_REFLECTION_MARKER = lambda: f"lzm{uuid.uuid4().hex[:10]}"

_STACK_RX = re.compile(
    r"(?:Exception|Error)(?:\s|:)|at\s+\S+\.\S+\(\S+\.java:|"
    r"Traceback\s+\(most|java\.lang\.|org\.springframework",
    re.IGNORECASE,
)


def _root_domain(asset: str) -> str:
    a = asset.strip().lower()
    if a.startswith("*."):
        a = a[2:]
    if "://" in a:
        a = a.split("://", 1)[1]
    a = a.split("/", 1)[0]
    return a


def _build_ua(intigriti_email: str) -> str:
    if intigriti_email:
        return f"Mozilla/5.0 lazyclaw {intigriti_email} (intigriti-bug-bounty)"
    return "Mozilla/5.0 lazyclaw (intigriti-bug-bounty)"


async def _certspotter_subs(parent: str) -> set[str]:
    """Fetch CT-log subdomains. Same approach as bounty_recon."""
    url = (
        f"https://api.certspotter.com/v1/issuances?domain={parent}"
        "&include_subdomains=true&expand=dns_names"
    )
    try:
        def _do() -> list[dict]:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 lazyclaw (intigriti-bug-bounty)"},
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        data = await asyncio.to_thread(_do)
    except Exception as exc:
        logger.warning("certspotter for %s failed: %r", parent, exc)
        return set()
    out: set[str] = set()
    for entry in data:
        for name in entry.get("dns_names", []):
            n = name.lower().lstrip("*.")
            out.add(n)
    return out


async def _resolve_a(host: str) -> bool:
    """Best-effort A-record check — only treat as live if it resolves."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "dig", "+short", "+time=2", "+tries=1", host,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        for line in out.decode().splitlines():
            line = line.strip()
            if re.match(r"^\d+\.\d+\.\d+\.\d+$", line):
                return True
        return False
    except Exception:
        return False


def _build_cookie_header(
    cookies: list[dict[str, Any]], host: str, path: str = "/",
) -> str:
    pairs: list[str] = []
    host_lc = host.lower()
    for c in cookies:
        domain = (c.get("domain") or "").lower().lstrip(".")
        if not domain:
            continue
        if not (host_lc == domain or host_lc.endswith("." + domain)):
            continue
        cpath = c.get("path") or "/"
        if not path.startswith(cpath):
            continue
        n, v = c.get("name"), c.get("value")
        if n is None or v is None:
            continue
        pairs.append(f"{n}={v}")
    return "; ".join(pairs)


def _send(
    url: str, ua: str, accept: str, cookie_header: str, timeout: int = 10,
) -> dict:
    """Synchronous one-shot HTTP GET, no redirect follow. Returns dict."""
    headers = {"User-Agent": ua, "Accept": accept}
    if cookie_header:
        headers["Cookie"] = cookie_header

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def http_error_302(self, req, fp, code, msg, headers):
            return None
        http_error_301 = http_error_303 = http_error_307 = http_error_302
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers=headers)
    t0 = time.time()
    try:
        r = opener.open(req, timeout=timeout)
        status = r.status
        body = r.read(60000)
        resp_h = dict(r.headers)
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read(8000) if e.fp else b""
        resp_h = dict(e.headers) if e.headers else {}
    except Exception as e:
        status = 0
        body = f"NETERR: {e!r}".encode()
        resp_h = {}
    elapsed_ms = int((time.time() - t0) * 1000)
    return {
        "status": int(status),
        "body": body,
        "headers": resp_h,
        "elapsed_ms": elapsed_ms,
    }


def _classify(
    url: str, name: str, marker: str, resp: dict,
) -> tuple[str, str, str] | None:
    """Return (vuln_class, severity, reason) if signal matched, else None."""
    status = resp["status"]
    body = resp["body"]
    body_text = body.decode("utf-8", errors="replace") if body else ""
    headers = resp["headers"]
    location = headers.get("Location") or headers.get("location") or ""

    # Open redirect — if reflection marker appears in Location host
    if name == "reflection_redirect" and 300 <= status < 400 and location:
        # Did we send the marker as redirect target? Marker is in body via the URL we sent.
        if "lzm-bounty" in (location or ""):
            return ("open_redirect", "medium",
                    f"Location header followed redirect= input ({location[:80]}); "
                    "open-redirect lead.")

    # Source map
    if name == "source_map" and status == 200 and b'"sourcesContent"' in body:
        return ("source_map_exposure", "low",
                f"Source map exposed ({len(body)} bytes); contains sourcesContent. "
                "Reconstructs admin client logic.")

    # .git / .env
    if name in ("dotgit", "dotenv") and status == 200 and len(body) > 20:
        if name == "dotgit" and b"[core]" in body:
            return ("dotfile_exposure", "high",
                    f".git/config served at {url} — full repo metadata exposed.")
        if name == "dotenv" and (b"=" in body and len(body) < 8000):
            return ("dotfile_exposure", "high",
                    f".env served at {url} — likely contains secrets.")

    # OpenAPI / Swagger
    if name in ("openapi", "swagger") and status == 200:
        if (b'"openapi"' in body or b'"swagger"' in body) and b'"paths"' in body:
            return ("api_spec_exposure", "low",
                    f"OpenAPI/Swagger spec at {url} ({len(body)} bytes); "
                    "expands attack surface for unauthenticated discovery.")

    # actuator
    if name == "actuator_env" and status == 200 and b"propertySources" in body:
        return ("dotfile_exposure", "high",
                f"Spring actuator/env exposed at {url}; contains config + secrets.")

    # Reflection — basic marker echo
    if name == "reflection_marker" and status == 200 and marker in body_text:
        # Crude check — if marker reflects without HTML-encoding (raw <,>,") it's
        # a reflection candidate. Real XSS confirmation happens manually.
        idx = body_text.find(marker)
        ctx = body_text[max(0, idx - 30): idx + 30]
        return ("reflection", "info",
                f"Marker {marker!r} reflected in body, context: {ctx!r}. "
                "Manual XSS verification required.")

    # Verbose 5xx with stack trace
    if 500 <= status < 600 and _STACK_RX.search(body_text):
        snippet = body_text[:300].replace("\n", " ")
        return ("verbose_error", "info",
                f"Server stack trace exposed at {url}: {snippet}")

    return None


class BountyHuntSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "bounty_hunt"

    @property
    def display_name(self) -> str:
        return "Bounty: hunt the registered program"

    @property
    def category(self) -> str:
        return "bounty"

    @property
    def description(self) -> str:
        return (
            "Run a deterministic probe matrix against every live in-scope "
            "host of a registered program. Fetches subdomains from CT "
            "logs, filters through ScopeChecker, A-resolves each, then "
            "probes a recipe of common low-tier-bug paths (source maps, "
            "OpenAPI/Swagger, .git/.env, actuator, GraphQL, reflection "
            "markers). Heuristic-classifies each response and saves "
            "matched signals as 'proposed' findings. Authenticated if "
            "bounty_login has captured cookies for the program. Audit-"
            "logged end-to-end. After it runs, review with "
            "bounty_list_findings + bounty_validate_finding."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "program_name": {"type": "string"},
                "max_hosts": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Cap on live hosts probed (default 20).",
                },
                "intigriti_email": {
                    "type": "string",
                    "description": "Tagged into User-Agent per Intigriti rules.",
                },
            },
            "required": ["program_name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        program_name = params["program_name"]
        max_hosts = int(params.get("max_hosts") or 20)
        intigriti_email = (params.get("intigriti_email") or "").strip()

        program = await store.get_program(self._config, user_id, program_name)
        if not program:
            return f"❌ No program '{program_name}'."
        if not program["enabled"]:
            return f"❌ Program '{program_name}' is disabled."

        try:
            from claude_bug_bounty import ScopeChecker
        except ImportError as exc:
            return f"❌ claude_bug_bounty not installed: {exc}"

        checker = ScopeChecker(
            domains=program["scope_assets"],
            excluded_domains=program["excluded_assets"] or None,
            excluded_classes=program["excluded_classes"] or None,
        )
        ua = _build_ua(intigriti_email)
        rate_rps = int(program.get("rate_limit_rps") or 5)
        min_interval = 1.0 / max(rate_rps, 1)
        cookies, _saved = await store.load_session_cookies(
            self._config, user_id, program_name,
        )

        # Phase 1 — CT-log subdomains. Also seed exact-host scope assets
        # directly: a program with `app.aikido.dev` (no wildcard) won't
        # surface in CT enum of `aikido.dev` parent unless certs name it
        # explicitly, so we always add it as a candidate.
        all_subs: set[str] = set()
        for asset in program["scope_assets"]:
            a = asset.strip().lower()
            if not a.startswith("*."):
                if "://" in a:
                    a = a.split("://", 1)[1]
                a = a.split("/", 1)[0]
                all_subs.add(a)
            base = _root_domain(asset)
            if not base:
                continue
            await store.write_audit(
                self._config, user_id, program_id=program["id"],
                target=f"crt:{base}", tool="certspotter", method="GET",
                decision="allow",
            )
            subs = await _certspotter_subs(base)
            all_subs.update(subs)

        # Phase 2 — scope filter
        in_scope = sorted(s for s in all_subs if checker.is_in_scope(s))
        if not in_scope:
            return f"Hunt {program_name}: 0 in-scope subdomains (CT pool: {len(all_subs)})."

        # Phase 3 — A-resolve to find live hosts
        live: list[str] = []
        for host in in_scope[:max_hosts * 2]:  # over-sample to absorb NXDOMAINs
            if await _resolve_a(host):
                live.append(host)
            if len(live) >= max_hosts:
                break

        # Phase 4 — probe recipe per live host
        findings: list[tuple[str, str, str, str, str]] = []
        # (url, vuln_class, severity, reason, finding_id)
        last_request_ts = 0.0
        marker = _REFLECTION_MARKER()

        for host in live:
            cookie_header = _build_cookie_header(cookies, host)
            base_url = f"https://{host}"

            recipe_extended = list(_RECIPE)
            # Add reflection probes per host (marker injected as path query)
            recipe_extended.append((f"/?_lzm={marker}", "reflection_marker",
                                    "text/html, */*"))
            recipe_extended.append((f"/?redirect=https://example.org/lzm-bounty",
                                    "reflection_redirect", "text/html, */*"))
            # Add source map (best-effort common bundle name)
            recipe_extended.append(("/static/scripts/main.js.map", "source_map",
                                    "application/json, */*"))

            for path, name, accept in recipe_extended:
                url = base_url + path
                if not checker.is_in_scope(url):
                    await store.write_audit(
                        self._config, user_id, program_id=program["id"],
                        target=url, tool="probe", method="GET",
                        decision="refuse",
                    )
                    continue

                # Rate limit
                gap = time.time() - last_request_ts
                if gap < min_interval:
                    await asyncio.sleep(min_interval - gap)
                last_request_ts = time.time()

                resp = await asyncio.to_thread(
                    _send, url, ua, accept, cookie_header, 10,
                )
                await store.write_audit(
                    self._config, user_id, program_id=program["id"],
                    target=url, tool="probe", method="GET",
                    decision="allow", response_code=resp["status"],
                )

                signal = _classify(url, name, marker, resp)
                if signal is None:
                    continue
                vuln_class, severity, reason = signal

                size = len(resp["body"])
                title = f"{vuln_class.replace('_', ' ').title()}: {host}{path}"
                poc = (
                    f"GET {url}\n"
                    f"User-Agent: {ua}\n"
                    f"Cookie: {'(set)' if cookie_header else '(none)'}\n\n"
                    f"Response: {resp['status']} ({size} bytes, "
                    f"{resp['headers'].get('Content-Type', '')})\n\n"
                    f"Signal: {reason}\n\n"
                    f"Body excerpt (first 600 chars):\n"
                    f"{resp['body'][:600].decode('utf-8', errors='replace')!r}"
                )
                try:
                    fid = await store.create_finding(
                        self._config, user_id,
                        program_id=program["id"],
                        title=title[:200],
                        vuln_class=vuln_class,
                        severity=severity,
                        target_url=url,
                        poc=poc,
                    )
                    findings.append((url, vuln_class, severity, reason, fid))
                except Exception:
                    logger.warning("create_finding failed for %s", url, exc_info=True)

        # Phase 5 — report
        lines = [
            f"## Hunt for **{program_name}** ({program['platform']})",
            "",
            f"- CT pool: {len(all_subs)} subdomains",
            f"- In scope: {len(in_scope)}",
            f"- Live (A-resolved): {len(live)}",
            f"- Authenticated: {'yes' if cookies else 'no'}",
            f"- **Signals matched: {len(findings)}**",
            "",
        ]
        if findings:
            lines.append("### Findings (saved as proposed)")
            for url, cls, sev, reason, fid in findings:
                lines.append(f"- `[{sev}]` {cls} — `{url}`")
                lines.append(f"  - {reason[:160]}")
                lines.append(f"  - id: `{fid}`")
            lines.append("")
            lines.append(
                f"Review: `bounty_list_findings program_name={program_name}` then "
                "`bounty_validate_finding finding_id=<id>` to run the 7-Question Gate."
            )
        else:
            lines.append(
                "No signals matched. The program is well-defended for the "
                "deterministic recipe. Try expanding the recipe, adding "
                "authenticated paths via OSINT, or running active scanners "
                "via the upstream `install_tools.sh` (subfinder + nuclei)."
            )
        return "\n".join(lines)
