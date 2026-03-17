from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DoctorConfig:
    """Configuration for mcp-lazydoctor."""

    # Root of the project to diagnose (defaults to cwd)
    project_root: Path = Path(".")

    # Tool paths — empty string means "use PATH lookup"
    ruff_path: str = ""
    pytest_path: str = ""
    mypy_path: str = ""

    # Safety limits
    max_file_size_kb: int = 500  # skip files larger than this
    max_fix_lines: int = 50  # refuse auto-fix patches larger than this
    test_timeout: int = 120  # seconds
    lint_timeout: int = 60  # seconds

    # Auto-fix behavior
    auto_fix_enabled: bool = True  # whether ruff --fix is allowed
    dry_run: bool = False  # if True, report but don't apply fixes

    # Git safety
    require_clean_git: bool = True  # refuse destructive ops on dirty worktree
    auto_commit: bool = False  # commit fixes automatically
    branch_prefix: str = "lazydoctor/"  # branch name prefix for fix branches


def load_config() -> DoctorConfig:
    root = os.getenv("LAZYDOCTOR_PROJECT_ROOT", ".")
    return DoctorConfig(
        project_root=Path(root).resolve(),
        ruff_path=os.getenv("LAZYDOCTOR_RUFF_PATH", ""),
        pytest_path=os.getenv("LAZYDOCTOR_PYTEST_PATH", ""),
        mypy_path=os.getenv("LAZYDOCTOR_MYPY_PATH", ""),
        max_file_size_kb=int(os.getenv("LAZYDOCTOR_MAX_FILE_KB", "500")),
        max_fix_lines=int(os.getenv("LAZYDOCTOR_MAX_FIX_LINES", "50")),
        test_timeout=int(os.getenv("LAZYDOCTOR_TEST_TIMEOUT", "120")),
        lint_timeout=int(os.getenv("LAZYDOCTOR_LINT_TIMEOUT", "60")),
        auto_fix_enabled=os.getenv("LAZYDOCTOR_AUTO_FIX", "true").lower() == "true",
        dry_run=os.getenv("LAZYDOCTOR_DRY_RUN", "false").lower() == "true",
        require_clean_git=os.getenv("LAZYDOCTOR_REQUIRE_CLEAN_GIT", "true").lower() == "true",
        auto_commit=os.getenv("LAZYDOCTOR_AUTO_COMMIT", "false").lower() == "true",
        branch_prefix=os.getenv("LAZYDOCTOR_BRANCH_PREFIX", "lazydoctor/"),
    )
