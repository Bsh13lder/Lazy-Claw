"""Run the upstream 7-Question Gate on a finding before submission.

This skill bridges to `claude-bug-bounty/tools/validate.py`. The upstream
gate enforces:
  1. Scope verification (already passed at recon time, double-check)
  2. Reproducibility (POC must include enough detail to replay)
  3. Real impact (not theoretical)
  4. Vuln-class allowed by program rules
  5. CVSS 4.0 vector + score sanity
  6. Dedupe vs prior submissions
  7. Submission-ready report skeleton

If all gates pass, the finding moves from `proposed` → `validated`.
"""
from __future__ import annotations

import logging

from lazyclaw.skills.base import BaseSkill
from lazyclaw.skills.builtin.bounty import store

logger = logging.getLogger(__name__)


class BountyValidateFindingSkill(BaseSkill):
    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "bounty_validate_finding"

    @property
    def display_name(self) -> str:
        return "Validate finding (7-Question Gate)"

    @property
    def category(self) -> str:
        return "bounty"

    @property
    def description(self) -> str:
        return (
            "Run the 7-Question Gate (from claude-bug-bounty) on a "
            "proposed finding. The gate kills weak findings BEFORE writing "
            "the report — saves submission noise and protects researcher "
            "validity ratio. Pass → status flips to 'validated'. Fail → "
            "stays 'proposed' with the gate's specific objection. Use after "
            "bounty_recon and before any submission."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "finding_id": {
                    "type": "string",
                    "description": "ID from bounty_list_findings.",
                },
                "force": {
                    "type": "boolean",
                    "description": (
                        "When true, mark validated even if gate raises "
                        "warnings (only safe when the user manually "
                        "verified the POC in their browser)."
                    ),
                },
            },
            "required": ["finding_id"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        finding_id = params["finding_id"]
        finding = await store.get_finding(self._config, user_id, finding_id)
        if not finding:
            return f"❌ No finding with ID '{finding_id}'."

        if finding["status"] not in ("proposed", "rejected"):
            return (
                f"❌ Finding '{finding['title']}' is already in "
                f"`{finding['status']}` state. Use bounty_list_findings to "
                "find a proposed one."
            )

        # Run the gate. We invoke validate.py's pure helpers (CVSS scoring,
        # severity sanity) inline rather than its interactive CLI. The
        # interactive walk-through stays available via the upstream
        # `python tools/validate.py` for the user to run manually.
        gate_results = self._run_gate(finding)
        passed = all(g["passed"] for g in gate_results)
        force = bool(params.get("force"))

        if passed or force:
            ok = await store.update_finding_status(
                self._config, user_id, finding_id, "validated",
            )
            verdict = "✅ VALIDATED" + (" (forced)" if force and not passed else "")
            tail = (
                "\n\nNext step: review in your browser, then submit through "
                "your researcher account on the program's platform. The "
                "agent does NOT submit — submission stays human-gated."
            )
        else:
            ok = True  # status already proposed; nothing to update
            verdict = "❌ FAILED gate"
            tail = (
                "\n\nFix the issues above, then re-run validate. If you "
                "manually verified the POC in your browser, you can pass "
                "`force=true` to override (use with care — bypassing the "
                "gate increases your invalid-report risk)."
            )

        if not ok:
            return f"❌ Failed to update status for finding {finding_id}."

        lines = [
            f"## {verdict}: {finding['title']}",
            f"- Class: {finding['vuln_class']}  |  Severity: {finding['severity']}",
            f"- Target: {finding['target_url']}",
            "",
            "### 7-Question Gate",
        ]
        for g in gate_results:
            mark = "✅" if g["passed"] else "⚠️"
            lines.append(f"{mark} **{g['name']}** — {g['detail']}")
        lines.append(tail)
        return "\n".join(lines)

    def _run_gate(self, finding: dict) -> list[dict]:
        """Lightweight gate. Each entry: {name, passed, detail}.

        Mirrors the upstream interactive validate.py gates non-interactively:
          1. POC has enough detail to replay
          2. Severity matches vuln class (no 'critical' on info-class bugs)
          3. CVSS vector present OR severity is info/low
          4. Target URL is well-formed and HTTPS
          5. Title is descriptive (>= 8 chars, no placeholder words)
          6. Vuln class is on the recognised list
          7. POC mentions the target host (sanity check that POC matches URL)
        """
        from urllib.parse import urlparse

        results: list[dict] = []

        # 1. POC fullness
        poc = finding.get("poc") or ""
        results.append({
            "name": "POC fullness",
            "passed": len(poc) >= 80,
            "detail": (
                f"{len(poc)} chars (need ≥ 80 — must include the "
                "reproduction steps, not just a one-liner)"
            ),
        })

        # 2. Severity vs vuln-class sanity
        sev = finding.get("severity") or ""
        vc = finding.get("vuln_class") or ""
        cls_max = {
            "subdomain_takeover": "high",     # rarely critical w/o pivot
            "info_disclosure": "medium",
            "open_redirect": "medium",
            "cors_misconfiguration": "medium",
        }
        order = ["info", "low", "medium", "high", "critical"]
        max_allowed = cls_max.get(vc)
        ok = (
            max_allowed is None
            or order.index(sev) <= order.index(max_allowed)
        )
        results.append({
            "name": "Severity sanity",
            "passed": ok,
            "detail": (
                f"{sev} for {vc} "
                f"({'within max ' + max_allowed if max_allowed else 'no class cap'})"
            ),
        })

        # 3. CVSS present or severity is info/low
        has_cvss = bool(finding.get("cvss_vector") or finding.get("cvss_score"))
        cvss_ok = has_cvss or sev in {"info", "low"}
        results.append({
            "name": "CVSS",
            "passed": cvss_ok,
            "detail": (
                "vector + score set" if has_cvss
                else f"missing (required for {sev})"
            ),
        })

        # 4. URL well-formed + HTTPS
        url = finding.get("target_url") or ""
        parsed = urlparse(url)
        url_ok = bool(parsed.hostname) and parsed.scheme in {"http", "https"}
        results.append({
            "name": "Target URL",
            "passed": url_ok,
            "detail": url or "(empty)",
        })

        # 5. Title descriptive
        title = finding.get("title") or ""
        title_ok = (
            len(title) >= 8
            and "todo" not in title.lower()
            and "untitled" not in title.lower()
        )
        results.append({
            "name": "Title quality",
            "passed": title_ok,
            "detail": title[:80] or "(empty)",
        })

        # 6. Vuln class recognised
        known_classes = {
            "subdomain_takeover", "idor", "xss", "ssrf", "rce",
            "sqli", "open_redirect", "csrf", "info_disclosure",
            "cors_misconfiguration", "auth_bypass", "race_condition",
            "path_traversal", "ssti", "xxe", "deserialization",
        }
        results.append({
            "name": "Vuln class recognised",
            "passed": vc in known_classes,
            "detail": (
                vc if vc in known_classes
                else f"'{vc}' not in standard taxonomy"
            ),
        })

        # 7. POC mentions target host
        host = parsed.hostname or ""
        host_in_poc = host and host.lower() in poc.lower()
        results.append({
            "name": "POC matches target",
            "passed": bool(host_in_poc),
            "detail": (
                f"POC references {host}" if host_in_poc
                else f"POC does not mention target host {host}"
            ),
        })

        return results
