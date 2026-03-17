"""Git operations for safe auto-fix workflows."""
from __future__ import annotations

import logging

from mcp_lazydoctor.config import DoctorConfig
from mcp_lazydoctor.runner import RunResult, run_tool

logger = logging.getLogger(__name__)


async def git_status(config: DoctorConfig) -> RunResult:
    """Get git status."""
    return await run_tool(
        ["git", "status", "--porcelain"],
        cwd=config.project_root,
        timeout=10,
    )


async def is_clean_worktree(config: DoctorConfig) -> bool:
    """Check if the working tree is clean (no uncommitted changes)."""
    result = await git_status(config)
    return result.success and result.stdout.strip() == ""


async def current_branch(config: DoctorConfig) -> str:
    """Get current branch name."""
    result = await run_tool(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=config.project_root,
        timeout=10,
    )
    return result.stdout.strip() if result.success else "unknown"


async def create_fix_branch(config: DoctorConfig, name: str) -> RunResult:
    """Create and checkout a new fix branch."""
    branch_name = f"{config.branch_prefix}{name}"
    return await run_tool(
        ["git", "checkout", "-b", branch_name],
        cwd=config.project_root,
        timeout=10,
    )


async def commit_fix(config: DoctorConfig, message: str) -> RunResult:
    """Stage all changes and commit."""
    # Stage
    stage_result = await run_tool(
        ["git", "add", "-A"],
        cwd=config.project_root,
        timeout=10,
    )
    if not stage_result.success:
        return stage_result

    # Commit
    return await run_tool(
        ["git", "commit", "-m", message],
        cwd=config.project_root,
        timeout=15,
    )


async def diff_stat(config: DoctorConfig) -> str:
    """Get a summary of uncommitted changes."""
    result = await run_tool(
        ["git", "diff", "--stat"],
        cwd=config.project_root,
        timeout=10,
    )
    staged = await run_tool(
        ["git", "diff", "--staged", "--stat"],
        cwd=config.project_root,
        timeout=10,
    )
    parts = []
    if result.stdout.strip():
        parts.append(f"Unstaged:\n{result.stdout.strip()}")
    if staged.stdout.strip():
        parts.append(f"Staged:\n{staged.stdout.strip()}")
    return "\n".join(parts) if parts else "No changes"


async def recent_commits(config: DoctorConfig, count: int = 5) -> str:
    """Get recent commit log."""
    result = await run_tool(
        ["git", "log", f"--oneline", f"-{count}"],
        cwd=config.project_root,
        timeout=10,
    )
    return result.stdout.strip() if result.success else result.stderr
