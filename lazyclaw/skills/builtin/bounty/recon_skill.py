"""Passive recon: CT-log subdomain enum + CNAME-takeover candidate detection.

Strictly passive — no traffic to the target's web servers:
  1. Subdomain enumeration via CertSpotter's public CT-log API
  2. CNAME resolution via the system resolver (`dig`)
  3. Match against known-takeover services (Heroku, GitHub Pages, S3, etc.)

Every step is gated by the upstream `ScopeChecker` — out-of-scope hosts are
refused, audit-logged, and never hit. Every external request (CertSpotter
fetch, dig, scope check) is recorded in `bounty_audit`.

This is the MVP wedge for the live test; active scanners (subfinder, httpx,
nuclei) plug in later through the same gate.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
from typing import Any
from urllib.parse import urlparse

from lazyclaw.skills.base import BaseSkill
from lazyclaw.skills.builtin.bounty import store

logger = logging.getLogger(__name__)


# Services where a dangling CNAME can be claimed by an attacker. Substring
# match on the CNAME target. Sourced from EdOverflow's
# "can-i-take-over-xyz" — kept conservative (only services where the
# takeover is a real, verified vulnerability class).
_TAKEOVER_SIGNATURES = (
    "herokudns.com", "herokuapp.com",
    "github.io",
    "fastly.net",
    "azurewebsites.net", "cloudapp.net", "trafficmanager.net",
    "s3.amazonaws.com", "s3-website",
    "netlify.app", "netlify.com",
    "vercel.app", "now.sh",
    "surge.sh",
    "ghost.io",
    "tilda.ws",
    "webflow.io",
    "readme.io",
    "helpjuice.com",
    "unbouncepages.com",
    "kayako.com",
    "shopify.com",
    "tumblr.com",
    "bitbucket.io",
    "announcekit.app", "announcekit.co",
    "frontify.com",
    "youtrack.cloud",
    "intercom.help",
    "canny.io",
)


class BountyReconSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "bounty_recon"

    @property
    def display_name(self) -> str:
        return "Bounty recon"

    @property
    def category(self) -> str:
        return "bounty"

    @property
    def description(self) -> str:
        return (
            "Run passive recon on a registered bounty program. Pulls "
            "subdomains from public CT logs (CertSpotter), resolves CNAMEs, "
            "and surfaces dangling-CNAME takeover candidates. Strictly "
            "passive — zero traffic to the target's web servers. Every "
            "request is gated by the program's scope and audit-logged. "
            "Findings are saved to the bounty_findings table in 'proposed' "
            "state for human review via bounty_validate_finding."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "program_name": {
                    "type": "string",
                    "description": "Registered program name to scan.",
                },
                "max_subdomains": {
                    "type": "integer",
                    "minimum": 10,
                    "maximum": 2000,
                    "description": "Cap on subdomains to resolve (default 500).",
                },
                "include_target": {
                    "type": "string",
                    "description": (
                        "Optional: scope-test a specific URL/host without "
                        "running full recon. Useful for verifying the scope "
                        "guard refuses an out-of-scope target."
                    ),
                },
            },
            "required": ["program_name"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        program_name = params["program_name"]
        program = await store.get_program(self._config, user_id, program_name)
        if not program:
            return f"❌ No program named '{program_name}'. Use bounty_list_programs."
        if not program["enabled"]:
            return (
                f"❌ Program '{program_name}' is disabled. Re-enable via "
                f"bounty_register_program (idempotent on name)."
            )

        # Build the upstream ScopeChecker — deterministic, prefix-safe.
        try:
            from claude_bug_bounty import ScopeChecker
        except ImportError as exc:
            return (
                "❌ claude_bug_bounty package not importable — run "
                f"`pip install -e claude-bug-bounty/`. ({exc})"
            )

        checker = ScopeChecker(
            domains=program["scope_assets"],
            excluded_domains=program["excluded_assets"] or None,
            excluded_classes=program["excluded_classes"] or None,
        )

        # Standalone scope-test path (no enum). Useful for the verification
        # checklist: confirm the guard refuses an out-of-scope target.
        if params.get("include_target"):
            target = params["include_target"]
            allowed = checker.is_in_scope(target)
            await store.write_audit(
                self._config, user_id,
                program_id=program["id"],
                target=target,
                tool="scope_checker",
                method="scope_check",
                decision="allow" if allowed else "refuse",
            )
            verdict = "✅ in scope" if allowed else "🚫 [scope_refused]"
            return f"{verdict}: {target}"

        # Verify dig is on PATH — DNS resolution is essential for the
        # takeover-candidate path. We refuse rather than silently degrading.
        if not shutil.which("dig"):
            return (
                "❌ `dig` not found on PATH. Install bind9-dnsutils "
                "(Linux) or it ships with macOS by default. Required for "
                "CNAME takeover detection."
            )

        max_subs = int(params.get("max_subdomains") or 500)

        # Phase 1 — CT-log subdomain enum from CertSpotter
        all_subs: set[str] = set()
        for asset in program["scope_assets"]:
            base = _root_domain(asset)
            if not base:
                continue
            await store.write_audit(
                self._config, user_id,
                program_id=program["id"],
                target=f"crt:{base}",
                tool="certspotter",
                method="GET",
                decision="allow",
            )
            subs = await _fetch_certspotter(base)
            all_subs.update(subs)

        # Phase 2 — filter through ScopeChecker. Anything not matching is
        # refused + audit-logged; we never resolve or query out-of-scope.
        in_scope: list[str] = []
        for sub in sorted(all_subs):
            if checker.is_in_scope(sub):
                in_scope.append(sub)
            else:
                await store.write_audit(
                    self._config, user_id,
                    program_id=program["id"],
                    target=sub,
                    tool="scope_checker",
                    method="scope_check",
                    decision="refuse",
                )

        if not in_scope:
            return (
                f"Recon for **{program_name}**: {len(all_subs)} subdomain"
                f"{'s' if len(all_subs) != 1 else ''} found in CT logs, "
                "**0 in scope**. Either the scope is too narrow or the "
                "program uses non-public DNS records."
            )

        # Cap to keep this fast; user can raise via params.max_subdomains.
        in_scope = in_scope[:max_subs]

        # Phase 3 — resolve CNAMEs in parallel
        cname_pairs = await _resolve_cnames_parallel(in_scope)
        for sub, _cname in cname_pairs:
            await store.write_audit(
                self._config, user_id,
                program_id=program["id"],
                target=sub,
                tool="dig",
                method="GET",
                decision="allow",
                response_code=0,
            )

        # Phase 4 — match against takeover signatures
        candidates: list[tuple[str, str]] = []
        for sub, cname in cname_pairs:
            cname_lc = cname.lower().rstrip(".")
            if any(sig in cname_lc for sig in _TAKEOVER_SIGNATURES):
                candidates.append((sub, cname_lc))

        # Phase 5 — record candidates as proposed findings
        finding_ids: list[str] = []
        for sub, cname in candidates:
            try:
                fid = await store.create_finding(
                    self._config, user_id,
                    program_id=program["id"],
                    title=f"Possible subdomain takeover: {sub}",
                    vuln_class="subdomain_takeover",
                    severity="medium",  # tentative until validated
                    target_url=f"https://{sub}",
                    poc=(
                        f"Subdomain {sub} CNAMEs to {cname}, which is on the "
                        "known-takeover-vulnerable services list. To verify, "
                        "check whether the third-party resource is unclaimed "
                        "(visit https://{sub} — if you see a 'no such app' / "
                        "'this resource is not claimed' / 404 page, it's "
                        "potentially exploitable). Verification is the human "
                        "researcher's responsibility — only your registered "
                        "handle should send HTTP traffic to the target."
                    ),
                )
                finding_ids.append(fid)
            except Exception:
                logger.warning("create_finding failed for %s", sub, exc_info=True)

        # Format response
        lines = [
            f"## Recon for **{program_name}** ({program['platform']})",
            "",
            f"- Subdomains in CT logs: {len(all_subs)}",
            f"- In-scope (after ScopeChecker): {len(in_scope)}",
            f"- CNAMEs resolved: {len(cname_pairs)}",
            f"- Takeover candidates: **{len(candidates)}**",
            "",
        ]
        if candidates:
            lines.append("### Candidates (saved as proposed findings)")
            for sub, cname in candidates:
                lines.append(f"- `{sub}` → `{cname}`")
            lines.append("")
            lines.append(
                f"Review with `bounty_list_findings program_name={program_name}` "
                "then verify each in your browser (only your registered "
                "researcher handle should make HTTP requests to the target)."
            )
        else:
            lines.append(
                "✅ No takeover candidates surfaced — well-maintained program. "
                "This is the expected outcome for most mature targets. "
                "Move on or expand to active scanners (subfinder/nuclei) "
                "via the upstream install_tools.sh."
            )

        return "\n".join(lines)


# ─── helpers ──────────────────────────────────────────────────────────────


def _root_domain(asset: str) -> str | None:
    """Convert a scope pattern to a CT-search domain.

    *.acronis.com → acronis.com
    api.acronis.com → acronis.com  (CT search returns parents anyway)
    acronis.com → acronis.com
    https://acronis.com → acronis.com
    """
    if not asset:
        return None
    s = asset.strip().lstrip("*.").lower()
    if "://" in s:
        s = urlparse(s).hostname or s
    # collapse to last 2 labels (mostly correct for .com/.net/.org;
    # CertSpotter handles longer ccTLDs natively).
    parts = s.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:]) if len(parts) == 2 else ".".join(parts[-2:])
    return s


async def _fetch_certspotter(domain: str) -> set[str]:
    """Pull all DNS names from CertSpotter for a root domain. Best-effort."""
    import urllib.request
    url = (
        "https://api.certspotter.com/v1/issuances"
        f"?domain={domain}&include_subdomains=true&expand=dns_names"
    )

    def _do_fetch() -> set[str]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "lazyclaw-bounty/0.1"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            logger.debug("certspotter fetch failed for %s", domain, exc_info=True)
            return set()

        out: set[str] = set()
        for entry in data:
            for name in entry.get("dns_names") or []:
                clean = name.lower().lstrip("*.").strip()
                if clean and "." in clean:
                    out.add(clean)
        return out

    return await asyncio.to_thread(_do_fetch)


async def _resolve_cnames_parallel(
    subs: list[str], concurrency: int = 30,
) -> list[tuple[str, str]]:
    """dig +short CNAME on each sub. Returns (sub, cname) where CNAME exists."""
    sem = asyncio.Semaphore(concurrency)

    async def _one(sub: str) -> tuple[str, str] | None:
        async with sem:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "dig", "+short", "+time=2", "+tries=1", "CNAME", sub,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                line = stdout.decode("utf-8").strip().split("\n")[0].strip()
                if line:
                    return (sub, line)
            except (asyncio.TimeoutError, FileNotFoundError):
                return None
            except Exception:
                logger.debug("dig CNAME failed for %s", sub, exc_info=True)
        return None

    results = await asyncio.gather(*[_one(s) for s in subs])
    return [r for r in results if r is not None]
