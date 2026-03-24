"""Auto-fixer: applies safe fixes to diagnosed issues."""
from __future__ import annotations

import logging

from mcp_lazydoctor.diagnostics import DiagnosticResult, run_command

logger = logging.getLogger(__name__)


async def auto_fix_lint(project_root: str, dry_run: bool = False) -> str:
    """Run ruff --fix on the project."""
    if dry_run:
        return "[dry-run] Would run: ruff check --fix ."
    code, stdout, stderr = await run_command(
        ["python", "-m", "ruff", "check", "--fix", "."],
        cwd=project_root,
    )
    return stdout or stderr or "No lint issues to fix."


async def apply_fixes(
    diagnostics: list[DiagnosticResult],
    project_root: str,
    dry_run: bool = False,
) -> list[str]:
    """Apply auto-fixes based on diagnostic results."""
    actions: list[str] = []
    for diag in diagnostics:
        if not diag.success and diag.tool == "ruff":
            result = await auto_fix_lint(project_root, dry_run)
            actions.append(f"[ruff fix] {result}")
    if not actions:
        actions.append("No auto-fixable issues found.")
    return actions
