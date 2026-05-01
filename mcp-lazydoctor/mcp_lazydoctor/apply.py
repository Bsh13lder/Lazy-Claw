"""Apply a Finding on a sandboxed audit branch.

Flow:
    1. Resolve / create today's audit branch (``lazydoctor/audit-YYYY-MM-DD``).
    2. Switch to it so the change never touches main.
    3. Edit the target file (pyproject.toml or BUNDLED_MCPS line).
    4. Run a per-MCP smoke verifier (``pip install -e .`` + ``pytest -q``
       if the MCP ships tests).
    5. On green → ``git commit`` and record the SHA on the finding row.
       On red → ``git reset --hard`` the audit branch and mark the
       finding ``failed`` so next week's audit re-prompts.

Dry-run (default for first rollout): every shell call is logged not
executed; FindingStore stamps the row ``approved`` (not ``applied``) and
the report carries ``dry_run=True`` so the bridge skill can prefix the
Telegram push with ``[dry-run]``.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from mcp_lazydoctor.config import LazyDoctorConfig
from mcp_lazydoctor.diagnostics import run_command
from mcp_lazydoctor.findings import Finding, FindingStore
from mcp_lazydoctor.git_ops import (
    git_branch_exists,
    git_checkout_branch,
    git_commit,
    git_create_branch,
    git_current_branch,
    git_reset_hard,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApplyResult:
    success: bool
    finding_id: str
    dry_run: bool
    branch: str
    applied_commit: str | None = None
    smoke_test_output: str = ""
    error: str = ""
    log: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "finding_id": self.finding_id,
            "dry_run": self.dry_run,
            "branch": self.branch,
            "applied_commit": self.applied_commit,
            "smoke_test_output": self.smoke_test_output[:4000],
            "error": self.error,
            "log": list(self.log),
        }


def _today_branch(prefix: str) -> str:
    return prefix + datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _hydrate_finding(row: dict) -> Finding:
    """FindingStore returns plain dicts (sqlite Row → dict). Rebuild a
    frozen Finding so the apply path has a typed handle."""
    return Finding(
        id=row["id"],
        kind=row["kind"],
        target=row["target"],
        title=row["title"],
        severity=row["severity"],
        current_version=row["current_version"],
        proposed_version=row["proposed_version"],
        proposed_action=row["proposed_action"],
        diff_or_command=row["diff_or_command"] or "",
        rationale=row["rationale"] or "",
        created_at=row["created_at"],
    )


# ── Edit primitives ──────────────────────────────────────────────────────

def _edit_pip_pin(
    project_root: Path, target: str, _current: str | None,
    proposed: str,
) -> tuple[bool, str]:
    """Bump a single dep in an MCP's pyproject.toml.

    ``target`` is ``"<mcp-dir>/pyproject.toml::<package>"`` per
    PipDepProducer. We rewrite any line in that file that mentions the
    package name with a numeric specifier so it now reads ``<pkg>>=<ver>``.
    """
    try:
        rel_path, package = target.split("::", 1)
    except ValueError:
        return False, f"unparseable target {target!r}"
    pyproject = project_root / rel_path
    if not pyproject.exists():
        return False, f"pyproject not found at {pyproject}"

    text = pyproject.read_text(encoding="utf-8")
    pkg_re = re.escape(package)
    # Match the dep entry: optional quotes, package name, optional
    # extras/markers, the existing specifier. Keep the surrounding quotes
    # / commas / whitespace untouched.
    pattern = re.compile(
        rf"(?P<prefix>[\"'])\s*(?P<pkg>{pkg_re})\s*"
        rf"(?P<extras>\[[^\]]*\])?\s*"
        rf"(?P<specifier>[^\"',]*)"
        rf"(?P<suffix>[\"'])",
    )
    new_pin = f">={proposed}"

    def _sub(m: re.Match) -> str:
        return (
            f"{m.group('prefix')}{m.group('pkg')}"
            f"{m.group('extras') or ''}{new_pin}{m.group('suffix')}"
        )

    new_text, n = pattern.subn(_sub, text, count=1)
    if n == 0:
        return False, f"could not locate {package} in {rel_path}"
    if new_text == text:
        return False, f"{package} already at {proposed} in {rel_path}"
    pyproject.write_text(new_text, encoding="utf-8")
    return True, str(pyproject.relative_to(project_root))


def _edit_npx_pin(
    project_root: Path, target: str, proposed: str,
) -> tuple[bool, str]:
    """Bump an npx pin inside ``lazyclaw/mcp/manager.py``.

    ``target`` is ``"BUNDLED_MCPS::<old-spec>"`` from NpxDepProducer.
    """
    try:
        _prefix, old_spec = target.split("::", 1)
    except ValueError:
        return False, f"unparseable target {target!r}"
    manager = project_root / "lazyclaw" / "mcp" / "manager.py"
    if not manager.exists():
        return False, f"manager.py not found at {manager}"

    # old_spec is the full "@scope/pkg@1.2.3" string. Replace with
    # "<pkg-without-version>@<proposed>".
    if old_spec.startswith("@"):
        slash = old_spec.find("/")
        at = old_spec.find("@", slash) if slash != -1 else -1
    else:
        at = old_spec.find("@")
    pkg = old_spec[:at] if at != -1 else old_spec
    new_spec = f"{pkg}@{proposed}"

    text = manager.read_text(encoding="utf-8")
    if old_spec not in text:
        return False, f"could not locate {old_spec!r} in manager.py"
    new_text = text.replace(old_spec, new_spec, 1)
    if new_text == text:
        return False, "manager.py text unchanged after substitution"
    manager.write_text(new_text, encoding="utf-8")
    return True, "lazyclaw/mcp/manager.py"


# ── Smoke verifier ───────────────────────────────────────────────────────

async def _smoke_install_and_test(
    project_root: Path, edited_path: str,
) -> tuple[bool, str]:
    """For a pip_bump that touched ``mcp-foo/pyproject.toml`` we run
    ``pip install -e mcp-foo`` then ``pytest`` inside that dir if it has
    a ``tests/`` directory. For npx_bump we skip the smoke test — there's
    nothing to install locally; the next agent invocation of the MCP will
    surface a real failure if the new version's protocol changed."""
    log_lines: list[str] = []

    if edited_path.startswith("mcp-") and edited_path.endswith("/pyproject.toml"):
        mcp_dir = project_root / edited_path.rsplit("/", 1)[0]
        log_lines.append(f"$ pip install -e {mcp_dir.relative_to(project_root)}")
        code, stdout, stderr = await run_command(
            ["python3", "-m", "pip", "install", "-e", str(mcp_dir)],
            cwd=str(project_root),
        )
        log_lines.append((stdout + stderr).strip()[-1500:])
        if code != 0:
            return False, "\n".join(log_lines)

        tests_dir = mcp_dir / "tests"
        if tests_dir.exists():
            log_lines.append(f"$ pytest -q ({tests_dir.relative_to(project_root)})")
            code, stdout, stderr = await run_command(
                ["python3", "-m", "pytest", "-q", str(tests_dir)],
                cwd=str(project_root),
            )
            log_lines.append((stdout + stderr).strip()[-1500:])
            if code != 0:
                return False, "\n".join(log_lines)
        else:
            log_lines.append("(no tests/ dir — smoke install only)")
        return True, "\n".join(log_lines)

    log_lines.append(f"smoke skipped for edited_path={edited_path!r}")
    return True, "\n".join(log_lines)


# ── Apply orchestrator ───────────────────────────────────────────────────

async def apply_finding(
    config: LazyDoctorConfig,
    finding_id: str,
    *,
    store: FindingStore,
    dry_run: bool | None = None,
) -> ApplyResult:
    if dry_run is None:
        dry_run = config.dry_run
    log: list[str] = []
    branch = _today_branch(config.audit_branch_prefix)
    project_root = Path(config.project_root)

    row = store.get(finding_id)
    if row is None:
        return ApplyResult(
            success=False, finding_id=finding_id, dry_run=dry_run,
            branch=branch,
            error=f"finding {finding_id!r} not found",
        )
    if row["status"] in ("applied",):
        return ApplyResult(
            success=False, finding_id=finding_id, dry_run=dry_run,
            branch=branch,
            error=f"finding already {row['status']}",
        )
    finding = _hydrate_finding(row)

    # ── 1. Branch + checkout ──────────────────────────────────────────
    if dry_run:
        log.append(f"[dry-run] would create+checkout branch {branch}")
    else:
        current = await git_current_branch(str(project_root))
        log.append(f"current branch: {current or '<detached>'}")
        if not await git_branch_exists(str(project_root), branch):
            ok, msg = await git_create_branch(str(project_root), branch)
            log.append(f"create branch: {msg}")
            if not ok:
                return ApplyResult(
                    success=False, finding_id=finding_id, dry_run=False,
                    branch=branch, error=msg, log=tuple(log),
                )
        ok, msg = await git_checkout_branch(str(project_root), branch)
        log.append(f"checkout: {msg}")
        if not ok:
            return ApplyResult(
                success=False, finding_id=finding_id, dry_run=False,
                branch=branch, error=msg, log=tuple(log),
            )

    # ── 2. Edit ───────────────────────────────────────────────────────
    proposed = finding.proposed_version or ""
    if not proposed:
        return ApplyResult(
            success=False, finding_id=finding_id, dry_run=dry_run,
            branch=branch, error="finding has no proposed_version",
            log=tuple(log),
        )
    if dry_run:
        log.append(
            f"[dry-run] would apply edit for kind={finding.kind} "
            f"target={finding.target} → {proposed}"
        )
        log.append(f"[dry-run] command:\n{finding.diff_or_command}")
        store.update_status(
            finding_id, "approved",
            reason="dry-run: edit + smoke test skipped",
        )
        return ApplyResult(
            success=True, finding_id=finding_id, dry_run=True,
            branch=branch, log=tuple(log),
        )

    if finding.kind == "pip_bump":
        ok, edited_or_err = _edit_pip_pin(
            project_root, finding.target, finding.current_version, proposed,
        )
    elif finding.kind == "npx_bump":
        ok, edited_or_err = _edit_npx_pin(
            project_root, finding.target, proposed,
        )
    else:
        ok, edited_or_err = (
            False, f"apply for kind={finding.kind!r} not implemented in MVP",
        )
    if not ok:
        log.append(f"edit failed: {edited_or_err}")
        store.update_status(
            finding_id, "failed", reason=edited_or_err,
        )
        return ApplyResult(
            success=False, finding_id=finding_id, dry_run=False,
            branch=branch, error=edited_or_err, log=tuple(log),
        )
    edited_path = edited_or_err
    log.append(f"edited: {edited_path}")

    # ── 3. Smoke test ─────────────────────────────────────────────────
    smoke_ok, smoke_log = await _smoke_install_and_test(
        project_root, edited_path,
    )
    if not smoke_ok:
        log.append("smoke test FAILED — resetting branch")
        log.append(smoke_log)
        # Hard-reset wipes the bad edit so the audit branch never carries
        # known-broken changes. The user can still re-trigger after a fix.
        await git_reset_hard(str(project_root))
        store.update_status(
            finding_id, "failed",
            reason=f"smoke test failed:\n{smoke_log[-500:]}",
        )
        return ApplyResult(
            success=False, finding_id=finding_id, dry_run=False,
            branch=branch, error="smoke test failed",
            smoke_test_output=smoke_log, log=tuple(log),
        )
    log.append("smoke test PASSED")

    # ── 4. Commit ─────────────────────────────────────────────────────
    msg = (
        f"lazydoctor: {finding.title} [finding-id: {finding.id}]\n\n"
        f"{finding.proposed_action}\n\n"
        f"Rationale: {finding.rationale}"
    )
    ok, sha_or_err = await git_commit(
        str(project_root), msg, paths=[edited_path],
    )
    if not ok:
        log.append(f"commit failed: {sha_or_err}")
        await git_reset_hard(str(project_root))
        store.update_status(
            finding_id, "failed", reason=f"commit failed: {sha_or_err}",
        )
        return ApplyResult(
            success=False, finding_id=finding_id, dry_run=False,
            branch=branch, error=sha_or_err,
            smoke_test_output=smoke_log, log=tuple(log),
        )
    log.append(f"committed: {sha_or_err}")
    store.update_status(
        finding_id, "applied", reason="smoke test passed",
        applied_commit=sha_or_err,
    )
    return ApplyResult(
        success=True, finding_id=finding_id, dry_run=False,
        branch=branch, applied_commit=sha_or_err,
        smoke_test_output=smoke_log, log=tuple(log),
    )


async def revert_finding(
    config: LazyDoctorConfig, finding_id: str, *, store: FindingStore,
) -> dict:
    """Best-effort revert. Stays in plan-of-record terms: never rewrites
    history — emits a follow-up commit on the same branch."""
    row = store.get(finding_id)
    if row is None:
        return {"success": False, "error": f"finding {finding_id!r} not found"}
    sha = row.get("applied_commit")
    if not sha:
        return {
            "success": False,
            "error": f"finding {finding_id!r} has no applied_commit "
                     f"(status={row.get('status')!r})",
        }
    project_root = Path(config.project_root)
    from mcp_lazydoctor.git_ops import git_revert_commit
    ok, message = await git_revert_commit(str(project_root), sha)
    if ok:
        store.update_status(
            finding_id, "rejected", reason=f"reverted via lazydoctor: {sha}",
        )
    return {"success": ok, "message": message, "reverted_commit": sha}


__all__ = ["ApplyResult", "apply_finding", "revert_finding"]
