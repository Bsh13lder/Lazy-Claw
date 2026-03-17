"""Auto-fix engine — applies safe fixes and verifies them."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from mcp_lazydoctor.config import DoctorConfig
from mcp_lazydoctor.diagnostics import (
    DiagnosticReport,
    full_checkup,
    run_lint,
    run_lint_fix,
    run_format,
)
from mcp_lazydoctor.git_ops import (
    is_clean_worktree,
    create_fix_branch,
    commit_fix,
    diff_stat,
    current_branch,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FixResult:
    """Result of an auto-fix attempt."""
    fixes_applied: int
    issues_before: int
    issues_after: int
    branch: str
    committed: bool
    diff: str
    details: str

    @property
    def improved(self) -> bool:
        return self.issues_after < self.issues_before

    def to_text(self) -> str:
        lines = [
            f"Fixes applied: {self.fixes_applied}",
            f"Issues before: {self.issues_before} -> after: {self.issues_after}",
            f"Branch: {self.branch}",
            f"Committed: {self.committed}",
        ]
        if self.diff:
            lines.append(f"\nChanges:\n{self.diff}")
        if self.details:
            lines.append(f"\nDetails:\n{self.details}")
        return "\n".join(lines)


async def auto_fix(
    config: DoctorConfig,
    target: str | None = None,
) -> FixResult:
    """Run auto-fix: lint fix + format, then verify improvement.

    Workflow:
    1. Run diagnostics (before count)
    2. Optionally create fix branch
    3. Run ruff --fix (safe fixes only)
    4. Run ruff format
    5. Run diagnostics again (after count)
    6. Optionally commit
    7. Return report
    """
    if not config.auto_fix_enabled:
        return FixResult(
            fixes_applied=0,
            issues_before=0,
            issues_after=0,
            branch="",
            committed=False,
            diff="",
            details="Auto-fix is disabled. Set LAZYDOCTOR_AUTO_FIX=true to enable.",
        )

    # Check git state
    branch = await current_branch(config)
    if config.require_clean_git and not await is_clean_worktree(config):
        return FixResult(
            fixes_applied=0,
            issues_before=0,
            issues_after=0,
            branch=branch,
            committed=False,
            diff="",
            details="Working tree has uncommitted changes. Commit or stash first, "
                    "or set LAZYDOCTOR_REQUIRE_CLEAN_GIT=false.",
        )

    # 1. Before count
    before_lint, _ = await run_lint(config, target)
    issues_before = len(before_lint)

    # 2. Create fix branch if auto-commit is enabled
    committed = False
    if config.auto_commit and not config.dry_run:
        branch_result = await create_fix_branch(config, "auto-fix")
        if branch_result.success:
            branch = f"{config.branch_prefix}auto-fix"

    # 3. Apply fixes
    details_parts: list[str] = []

    if not config.dry_run:
        fix_result = await run_lint_fix(config, target)
        details_parts.append(f"ruff --fix: {fix_result.output[:500]}")

        fmt_result = await run_format(config, target, check_only=False)
        details_parts.append(f"ruff format: {fmt_result.output[:500]}")
    else:
        details_parts.append("DRY RUN: no changes applied")

    # 4. After count
    after_lint, _ = await run_lint(config, target)
    issues_after = len(after_lint)
    fixes_applied = max(0, issues_before - issues_after)

    # 5. Get diff
    diff = await diff_stat(config)

    # 6. Commit if configured
    if config.auto_commit and not config.dry_run and fixes_applied > 0:
        commit_result = await commit_fix(
            config,
            f"fix(lazydoctor): auto-fix {fixes_applied} lint issues",
        )
        committed = commit_result.success
        if committed:
            details_parts.append(f"Committed: {commit_result.output[:200]}")

    return FixResult(
        fixes_applied=fixes_applied,
        issues_before=issues_before,
        issues_after=issues_after,
        branch=branch,
        committed=committed,
        diff=diff,
        details="\n".join(details_parts),
    )
