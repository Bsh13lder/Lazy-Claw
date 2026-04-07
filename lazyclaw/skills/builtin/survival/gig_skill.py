"""Start working on an accepted gig via Claude Code MCP."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from lazyclaw.skills.base import BaseSkill

logger = logging.getLogger(__name__)


class StartGigSkill(BaseSkill):
    """Start working on a gig — spawns Claude Code MCP background task."""

    def __init__(self, config=None, registry=None) -> None:
        self._config = config
        self._registry = registry

    @property
    def name(self) -> str:
        return "start_gig"

    @property
    def description(self) -> str:
        return (
            "Start working on an accepted gig. Spawns a background task that uses "
            "Claude Code MCP to implement the solution, write tests, and auto-review. "
            "You (the founder) review the final result before submission. "
            "Usage: 'start gig 1' or 'start working on Build REST API'"
        )

    @property
    def category(self) -> str:
        return "survival"

    @property
    def permission_hint(self) -> str:
        return "ask"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "gig_reference": {
                    "type": "string",
                    "description": "Gig number from status, title, or gig ID",
                },
                "client_instructions": {
                    "type": "string",
                    "description": "Additional requirements from the client",
                },
            },
            "required": ["gig_reference"],
        }

    async def execute(self, user_id: str, params: dict) -> str:
        from lazyclaw.survival.gig import get_gig, list_gigs, update_gig_status
        from lazyclaw.survival.profile import get_profile

        ref = params.get("gig_reference", "")
        client_instructions = params.get("client_instructions", "")

        # Find the gig
        gig = await self._find_gig(user_id, ref)
        if gig is None:
            return f"Gig '{ref}' not found. Use 'survival status' to see your gigs."

        if gig.status not in ("applied", "hired", "found"):
            return (
                f"Gig '{gig.title}' is in status '{gig.status}'. "
                f"Can only start gigs that are applied, hired, or found."
            )

        # Create workspace directory
        safe_title = re.sub(r"[^\w\s-]", "", gig.title)[:30].strip().replace(" ", "_")
        workspace_dir = Path(self._config.database_dir) / "workspaces" / f"{gig.id[:8]}_{safe_title}"
        workspace_dir.mkdir(parents=True, exist_ok=True)

        # Transition to working
        await update_gig_status(
            self._config, user_id, gig.id, "working",
            workspace_path=str(workspace_dir),
        )

        # Build the work instruction for background task
        profile = await get_profile(self._config, user_id)
        work_instruction = self._build_work_instruction(
            gig, profile, client_instructions, workspace_dir,
        )

        # Submit as background task via TaskRunner
        task_runner = self._find_task_runner()
        if task_runner is None:
            return (
                f"Gig '{gig.title}' marked as WORKING.\n"
                f"Workspace: {workspace_dir}\n\n"
                f"TaskRunner not available — work manually using Claude Code:\n"
                f"{work_instruction}"
            )

        try:
            task_id = await task_runner.submit(
                user_id,
                instruction=work_instruction,
                timeout=3600,  # 1 hour for gig work
                name=f"gig_{gig.id[:8]}",
            )
        except Exception as exc:
            logger.warning("Failed to submit gig work to TaskRunner: %s", exc)
            return (
                f"Gig '{gig.title}' marked as WORKING but background task failed.\n"
                f"Error: {exc}\n"
                f"Work manually in: {workspace_dir}"
            )

        return (
            f"Started working on: **{gig.title}**\n\n"
            f"Workspace: {workspace_dir}\n"
            f"Background task: {task_id[:8]}\n"
            f"Timeout: 1 hour\n\n"
            f"LazyClaw is implementing the solution using Claude Code.\n"
            f"You'll be notified on Telegram when work is done.\n"
            f"Then you review as the final boss before submission."
        )

    def _build_work_instruction(
        self, gig, profile, client_instructions: str, workspace_dir: Path,
    ) -> str:
        desc = gig.description[:1000] if gig.description else "See job URL for details."
        extra = f"\n\nAdditional client instructions:\n{client_instructions}" if client_instructions else ""

        return (
            f"You are LazyClaw, an AI agent executing freelance work.\n\n"
            f"JOB: {gig.title}\n"
            f"PLATFORM: {gig.platform}\n"
            f"BUDGET: {gig.budget}\n"
            f"DESCRIPTION:\n{desc}{extra}\n\n"
            f"WORKSPACE: {workspace_dir}\n\n"
            f"YOUR SKILLS: {', '.join(profile.skills)}\n\n"
            f"INSTRUCTIONS:\n"
            f"1. Read the full job description carefully\n"
            f"2. Create the project in the workspace directory\n"
            f"3. Implement the complete solution\n"
            f"4. Write tests (pytest, jest, etc. as appropriate)\n"
            f"5. Run tests and fix any failures\n"
            f"6. Create a README.md with setup instructions\n"
            f"7. Verify everything works end-to-end\n\n"
            f"QUALITY STANDARDS:\n"
            f"- Production-ready code\n"
            f"- Error handling on all external calls\n"
            f"- No hardcoded secrets\n"
            f"- Clean, readable code with comments where needed\n"
            f"- All tests passing\n\n"
            f"When done, list all files created and a summary of what was built."
        )

    async def _find_gig(self, user_id: str, ref: str):
        """Find gig by number, title substring, or ID."""
        from lazyclaw.survival.gig import get_gig, list_gigs

        # Try as gig ID
        if len(ref) > 8:
            gig = await get_gig(self._config, user_id, ref)
            if gig:
                return gig

        # Try as number from status list
        if ref.isdigit():
            gigs = await list_gigs(self._config, user_id, limit=20)
            active = [g for g in gigs if g.status in (
                "applied", "hired", "found", "working", "review", "delivered",
            )]
            idx = int(ref) - 1
            if 0 <= idx < len(active):
                return active[idx]

        # Try as title substring
        gigs = await list_gigs(self._config, user_id, limit=50)
        ref_lower = ref.lower()
        for gig in gigs:
            if ref_lower in gig.title.lower():
                return gig

        return None

    def _find_task_runner(self):
        """Find TaskRunner instance from registry or imports."""
        try:
            from lazyclaw.runtime.task_runner import _global_runner
            return _global_runner
        except (ImportError, AttributeError):
            logger.debug("TaskRunner not available for gig execution", exc_info=True)
            return None
