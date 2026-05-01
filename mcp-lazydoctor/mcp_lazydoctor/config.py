from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class LazyDoctorConfig:
    project_root: str = "."
    auto_fix_enabled: bool = True
    # Default True for first rollout — every audit-apply path logs the
    # exact shell command + git diff but does NOT execute. Flip to False
    # via env LAZYDOCTOR_DRY_RUN=false (or the lazydoctor_setup NL skill)
    # once the proposed actions look right.
    dry_run: bool = True
    # Where bundled MCPs live. Audit producers walk ``mcps_root/mcp-*/`` to
    # find pyproject.toml dep declarations.
    mcps_root: str = "."
    # SQLite path for the FindingStore. Lives outside lazyclaw's main DB
    # on purpose — keeps lazydoctor process-isolated.
    findings_db: str = ".lazydoctor/findings.sqlite"
    # Branch prefix for the apply path. Date suffix (YYYY-MM-DD) is appended
    # at apply time so each weekly audit gets its own branch.
    audit_branch_prefix: str = "lazydoctor/audit-"


def _bool(env_value: str | None, default: bool) -> bool:
    if env_value is None:
        return default
    return env_value.strip().lower() in ("1", "true", "yes", "y", "on")


def load_config() -> LazyDoctorConfig:
    project_root = os.environ.get("LAZYDOCTOR_PROJECT_ROOT", os.getcwd())
    return LazyDoctorConfig(
        project_root=project_root,
        auto_fix_enabled=_bool(os.environ.get("LAZYDOCTOR_AUTO_FIX"), True),
        dry_run=_bool(os.environ.get("LAZYDOCTOR_DRY_RUN"), True),
        mcps_root=os.environ.get("LAZYDOCTOR_MCPS_ROOT", project_root),
        findings_db=os.environ.get(
            "LAZYDOCTOR_FINDINGS_DB",
            os.path.join(project_root, ".lazydoctor", "findings.sqlite"),
        ),
        audit_branch_prefix=os.environ.get(
            "LAZYDOCTOR_AUDIT_BRANCH_PREFIX", "lazydoctor/audit-"
        ),
    )
