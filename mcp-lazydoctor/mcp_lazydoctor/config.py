from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class LazyDoctorConfig:
    project_root: str = "."
    auto_fix_enabled: bool = True
    dry_run: bool = False


def load_config() -> LazyDoctorConfig:
    return LazyDoctorConfig(
        project_root=os.environ.get("LAZYDOCTOR_PROJECT_ROOT", os.getcwd()),
        auto_fix_enabled=os.environ.get("LAZYDOCTOR_AUTO_FIX", "true").lower() == "true",
        dry_run=os.environ.get("LAZYDOCTOR_DRY_RUN", "false").lower() == "true",
    )
