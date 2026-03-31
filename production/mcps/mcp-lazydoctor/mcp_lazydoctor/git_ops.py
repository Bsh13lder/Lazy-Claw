"""Git operations for self-healing workflows."""
from __future__ import annotations

import logging

from mcp_lazydoctor.diagnostics import run_command

logger = logging.getLogger(__name__)


async def git_status(project_root: str) -> str:
    """Run git status and return output."""
    code, stdout, stderr = await run_command(
        ["git", "status", "--short"], cwd=project_root,
    )
    return stdout or stderr


async def git_diff(project_root: str) -> str:
    """Run git diff and return output."""
    code, stdout, stderr = await run_command(
        ["git", "diff", "--stat"], cwd=project_root,
    )
    return stdout or stderr


async def git_stash(project_root: str) -> str:
    """Stash current changes."""
    code, stdout, stderr = await run_command(
        ["git", "stash"], cwd=project_root,
    )
    return stdout or stderr


async def git_stash_pop(project_root: str) -> str:
    """Pop the most recent stash."""
    code, stdout, stderr = await run_command(
        ["git", "stash", "pop"], cwd=project_root,
    )
    return stdout or stderr
