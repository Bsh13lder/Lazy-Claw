"""Audit bridge — Telegram approval glue between the lazydoctor MCP
(in its own process) and LazyClaw's checkpoint + task_runner + push
primitives (in this process).

The lazydoctor MCP returns findings as plain JSON. The brain's cron
instruction tells it to fan out: for each finding, call
``lazydoctor_review_finding`` (this skill). That:

  1. Calls ``request_checkpoint`` — blocks (up to 10 min) waiting on
     the user's Approve/Reject in Telegram or Web UI.
  2. On approve → submits a background task that asks the brain to call
     ``doctor_apply_finding`` against the lazydoctor MCP. The apply path
     itself honours config.dry_run on the lazydoctor side.
  3. On reject → calls back into lazydoctor via the brain to mark the
     finding rejected. The bridge can't write to lazydoctor's sqlite
     directly because the MCP runs in a separate process.
  4. Pushes a one-line summary to Telegram either way.

A second tool, ``lazydoctor_summarize_pending``, lets the user ask
"what's pending lazydoctor approval?" without re-running an audit.
"""
from __future__ import annotations

import logging

from lazyclaw.browser.checkpoints import request_checkpoint
from lazyclaw.notifications.push import push_telegram
from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


def _format_card(
    finding_id: str, title: str, severity: str,
    proposed_action: str, diff_or_command: str,
) -> str:
    """Compact approval card. Telegram trims at 4096 chars; checkpoint
    detail is shown inline so we keep it under 1200."""
    sev_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(severity, "•")
    cmd_block = diff_or_command.strip()[:600]
    if len(diff_or_command) > 600:
        cmd_block += "\n…(truncated)…"
    return (
        f"{sev_icon} *{title}*\n"
        f"Severity: {severity}\n"
        f"Finding: `{finding_id}`\n\n"
        f"{proposed_action.strip()[:400]}\n\n"
        f"```\n{cmd_block}\n```\n\n"
        f"Approve to apply on the audit branch (sandboxed, no auto-merge to main)."
    )


class LazydoctorReviewFindingSkill(BaseSkill):
    """Surface a single finding for Telegram approval and dispatch the
    apply on green."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazydoctor_review_finding"

    @property
    def description(self) -> str:
        return (
            "Surface ONE lazydoctor finding to the user for approval. "
            "Blocks until the user approves or rejects (10-min timeout). "
            "On approve, submits a background task that calls "
            "doctor_apply_finding. On reject, marks the finding rejected. "
            "Used by the cron-job brain instruction; rarely called by hand."
        )

    @property
    def category(self) -> str:
        # New 'audit' category — defaults to ASK in
        # permissions/models.py. The user sees one approval per finding;
        # the skill itself is gated to ensure the brain can't silently
        # spam approvals during an unsupervised audit run.
        return "audit"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "finding_id": {"type": "string"},
                "title": {"type": "string"},
                "severity": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                },
                "proposed_action": {"type": "string"},
                "diff_or_command": {
                    "type": "string",
                    "description": "Shell command or unified diff that would be applied.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 30,
                    "maximum": 3600,
                    "description": "Approval wait timeout. Default 600s.",
                },
            },
            "required": [
                "finding_id", "title", "severity",
                "proposed_action", "diff_or_command",
            ],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        finding_id = params["finding_id"]
        title = params["title"]
        severity = params.get("severity", "medium")
        proposed_action = params.get("proposed_action", "")
        diff_or_command = params.get("diff_or_command", "")
        timeout = int(params.get("timeout_seconds", 600))

        # ── 1. Block on user approval ─────────────────────────────────
        decision = await request_checkpoint(
            user_id,
            name=f"lazydoctor:{finding_id}",
            detail=_format_card(
                finding_id, title, severity,
                proposed_action, diff_or_command,
            ),
            timeout=timeout,
        )

        if not decision.get("approved"):
            reason = decision.get("reason") or "rejected"
            # Mark the finding rejected via the brain (the bridge can't
            # write to lazydoctor's sqlite directly). We return a
            # follow-up instruction string that the brain executes next
            # turn — keeps this skill stateless w.r.t. the MCP.
            await push_telegram(
                self._config,
                f"❌ Lazydoctor finding `{finding_id}` rejected ({reason}).",
            )
            return (
                f"REJECTED finding {finding_id}: {reason}. "
                f"Call doctor_mark_finding(finding_id={finding_id!r}, "
                f"status='rejected', reason={reason!r}) to persist this on "
                f"lazydoctor's side."
            )

        # ── 2. On approve, dispatch apply via task_runner ────────────
        from lazyclaw.runtime import task_runner as _tr_mod
        runner = _tr_mod._task_runner_instance
        if runner is None:
            await push_telegram(
                self._config,
                f"⚠️ Lazydoctor finding `{finding_id}` approved but task "
                f"runner is not initialized — apply skipped.",
            )
            return (
                f"APPROVED finding {finding_id} but TaskRunner singleton "
                f"unavailable. Call doctor_apply_finding manually."
            )

        instruction = (
            f"[LAZYDOCTOR_APPLY] User approved lazydoctor finding "
            f"{finding_id} ({title}). Call doctor_apply_finding with "
            f"finding_id={finding_id!r}. On success push a Telegram "
            f"message: \"✅ Applied lazydoctor:{finding_id} — \" + "
            f"the returned applied_commit. On failure push the error "
            f"reason. Then stop."
        )
        try:
            task_id = await runner.submit(
                user_id,
                instruction=instruction,
                name=f"lazydoctor-apply-{finding_id}",
                source="cron",
            )
        except Exception as exc:
            logger.exception("Failed to submit lazydoctor apply task")
            await push_telegram(
                self._config,
                f"⚠️ Lazydoctor finding `{finding_id}` approved but apply "
                f"submission failed: {exc}",
            )
            return f"APPROVED finding {finding_id} but submit failed: {exc}"

        await push_telegram(
            self._config,
            f"✅ Lazydoctor finding `{finding_id}` approved — "
            f"applying in background (task `{task_id}`).",
        )
        return (
            f"APPROVED finding {finding_id}. Apply dispatched as task "
            f"{task_id}. Move on to the next finding."
        )


class LazydoctorSummarizePendingSkill(BaseSkill):
    """Read-only readout of pending findings without re-running an audit."""

    def __init__(self, config=None) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "lazydoctor_summarize_pending"

    @property
    def description(self) -> str:
        return (
            "List pending lazydoctor findings (not yet approved/rejected). "
            "Returns instruction telling the brain to call doctor_list_findings "
            "and format the output. Cheap — no audit run. Use when the user "
            "asks 'what's pending lazydoctor approval'."
        )

    @property
    def category(self) -> str:
        return "utility"

    @property
    def permission_hint(self) -> str:
        return "allow"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1, "maximum": 200,
                    "description": "Max rows to return. Default 20.",
                },
            },
        }

    async def execute(self, user_id: str, params: dict) -> str:
        limit = int((params or {}).get("limit", 20))
        return (
            f"Call doctor_list_findings(status='pending', limit={limit}) "
            f"on the lazydoctor MCP and summarize the rows for the user "
            f"(id, title, severity, current → proposed)."
        )
