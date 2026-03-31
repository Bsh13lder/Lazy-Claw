"""Orchestrates diagnose-fix-verify cycles."""
from __future__ import annotations

import logging

from mcp_lazydoctor.config import LazyDoctorConfig
from mcp_lazydoctor.diagnostics import diagnose_project, DiagnosticResult
from mcp_lazydoctor.fixer import apply_fixes

logger = logging.getLogger(__name__)


async def run_heal_cycle(config: LazyDoctorConfig) -> dict:
    """Run a full diagnose -> fix -> verify cycle."""
    # Step 1: Diagnose
    diagnostics = await diagnose_project(config.project_root)
    all_pass = all(d.success for d in diagnostics)

    if all_pass:
        return {
            "status": "healthy",
            "diagnostics": [{"tool": d.tool, "success": d.success} for d in diagnostics],
            "fixes_applied": [],
        }

    # Step 2: Fix
    fixes: list[str] = []
    if config.auto_fix_enabled:
        fixes = await apply_fixes(diagnostics, config.project_root, config.dry_run)

    # Step 3: Re-diagnose
    post_diagnostics = await diagnose_project(config.project_root)
    post_pass = all(d.success for d in post_diagnostics)

    return {
        "status": "healed" if post_pass else "needs_attention",
        "diagnostics": [
            {"tool": d.tool, "success": d.success, "errors": len(d.errors)}
            for d in diagnostics
        ],
        "fixes_applied": fixes,
        "post_fix_status": [
            {"tool": d.tool, "success": d.success}
            for d in post_diagnostics
        ],
    }
