"""Git operations for self-healing + audit-apply workflows.

The audit applier (apply.py) lands every approved finding on a sandboxed
``lazydoctor/audit-YYYY-MM-DD`` branch. main is never touched directly —
the user merges manually after they're satisfied with a week's worth of
findings, and ``git_revert_commit`` is the rollback button.
"""
from __future__ import annotations

import logging

from mcp_lazydoctor.diagnostics import run_command

logger = logging.getLogger(__name__)


# ── Inspection ────────────────────────────────────────────────────────────

async def git_status(project_root: str) -> str:
    """Run git status and return output."""
    _code, stdout, stderr = await run_command(
        ["git", "status", "--short"], cwd=project_root,
    )
    return stdout or stderr


async def git_diff(project_root: str) -> str:
    """Run git diff and return output."""
    _code, stdout, stderr = await run_command(
        ["git", "diff", "--stat"], cwd=project_root,
    )
    return stdout or stderr


async def git_current_branch(project_root: str) -> str:
    """Return the current branch name (or empty string on detached HEAD)."""
    code, stdout, stderr = await run_command(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=project_root,
    )
    if code != 0:
        logger.debug("git_current_branch failed: %s", stderr)
        return ""
    branch = stdout.strip()
    return "" if branch == "HEAD" else branch


# ── Stash (kept for backwards compatibility / heal flow) ──────────────────

async def git_stash(project_root: str) -> str:
    _code, stdout, stderr = await run_command(
        ["git", "stash"], cwd=project_root,
    )
    return stdout or stderr


async def git_stash_pop(project_root: str) -> str:
    _code, stdout, stderr = await run_command(
        ["git", "stash", "pop"], cwd=project_root,
    )
    return stdout or stderr


# ── Branch management ────────────────────────────────────────────────────

async def git_branch_exists(project_root: str, name: str) -> bool:
    code, _stdout, _stderr = await run_command(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{name}"],
        cwd=project_root,
    )
    return code == 0


async def git_create_branch(
    project_root: str, name: str, base: str = "HEAD",
) -> tuple[bool, str]:
    """Create a new branch pointing at ``base``. No-op if it already exists.
    Returns ``(success, message)``.
    """
    if await git_branch_exists(project_root, name):
        return True, f"branch {name} already exists"
    code, stdout, stderr = await run_command(
        ["git", "branch", name, base], cwd=project_root,
    )
    success = code == 0
    return success, (stdout + stderr).strip() or (
        f"created {name}" if success else f"failed to create {name}"
    )


async def git_checkout_branch(
    project_root: str, name: str, create: bool = False,
) -> tuple[bool, str]:
    """Switch to ``name``. With ``create=True`` create it if missing
    (equivalent to ``git checkout -b``)."""
    args = ["git", "checkout"]
    if create and not await git_branch_exists(project_root, name):
        args.append("-b")
    args.append(name)
    code, stdout, stderr = await run_command(args, cwd=project_root)
    success = code == 0
    return success, (stdout + stderr).strip()


# ── Commit + revert ──────────────────────────────────────────────────────

async def git_commit(
    project_root: str, message: str, paths: list[str] | None = None,
) -> tuple[bool, str]:
    """Stage ``paths`` (or everything if None) and commit with ``message``.

    Returns ``(success, commit_sha_or_error)``. On success the second
    element is the new commit's full SHA — apply.py records it on the
    finding row so ``doctor_revert_finding`` can revert it later.
    """
    if paths:
        code, _stdout, stderr = await run_command(
            ["git", "add", "--", *paths], cwd=project_root,
        )
    else:
        code, _stdout, stderr = await run_command(
            ["git", "add", "-A"], cwd=project_root,
        )
    if code != 0:
        return False, f"git add failed: {stderr.strip()}"

    code, _stdout, stderr = await run_command(
        ["git", "commit", "-m", message, "--no-verify"], cwd=project_root,
    )
    if code != 0:
        return False, f"git commit failed: {stderr.strip()}"

    # Resolve the new HEAD SHA.
    code, sha_out, sha_err = await run_command(
        ["git", "rev-parse", "HEAD"], cwd=project_root,
    )
    if code != 0:
        return False, f"git rev-parse HEAD failed: {sha_err.strip()}"
    return True, sha_out.strip()


async def git_revert_commit(
    project_root: str, sha: str,
) -> tuple[bool, str]:
    """Create a follow-up commit that undoes ``sha``. Safe — never
    rewrites history."""
    code, stdout, stderr = await run_command(
        ["git", "revert", "--no-edit", sha], cwd=project_root,
    )
    success = code == 0
    return success, (stdout + stderr).strip()


async def git_reset_hard(
    project_root: str, ref: str = "HEAD",
) -> tuple[bool, str]:
    """Hard-reset the working tree. Used by apply.py when smoke tests fail
    so the audit branch never carries broken changes."""
    code, stdout, stderr = await run_command(
        ["git", "reset", "--hard", ref], cwd=project_root,
    )
    return code == 0, (stdout + stderr).strip()
